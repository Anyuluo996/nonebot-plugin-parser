"""异步解析 API（api/ 包）的行为测试。

覆盖：鉴权与限流、提交判定顺序（去重→422→429）、轮询信封与现签媒体 URL、
段导出三形态（file:///base64:///http）与占位符落库、metadata 媒体改写、
路径穿越全组、签名有效/过期/篡改、DELETE 取消语义、配额全生命周期、
TTL 清理与 stale 标记顺序、store 并发、错误脱敏。

stub parser / stub renderer 在测试函数内定义 —— NoneBot 初始化发生在 collect
之后，模块顶层 import 插件包会失败（见 conftest init_nonebot fixture）。
路由挂独立 FastAPI app（不依赖 driver 配置），fastapi 由 dev 依赖提供。
"""

import asyncio
import json
import re
import sqlite3
import time
from typing import ClassVar
from urllib.parse import quote

import pytest

AUTH = {"Authorization": "Bearer test-token-123"}

_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
_JPEG = b"\xff\xd8\xff" + b"fake-jpeg-body"


# ── 公共夹具与 stub ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def api_env(tmp_path, monkeypatch):
    """每个测试独立的 DB / 媒体目录 / 配置；清空限流桶与任务注册表。"""
    from nonebot_plugin_parser.api import media, runner, routes, store
    from nonebot_plugin_parser.config import pconfig

    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "api_tasks.db")
    monkeypatch.setattr(media, "MEDIA_ROOT", tmp_path / "api_media")
    monkeypatch.setattr(pconfig, "parser_api_token", "test-token-123")
    monkeypatch.setattr(pconfig, "parser_api_base_url", "http://media.test")
    monkeypatch.setattr(pconfig, "parser_api_media_sig_ttl", 3600)
    monkeypatch.setattr(pconfig, "parser_api_task_ttl", 3600)
    monkeypatch.setattr(pconfig, "parser_use_base64", False)  # 段导出走 file:// 形态，可控
    media.quota.rebuild()  # 配额计数器随新目录归零（隔离上一测试的占用）
    routes.reset_sign_key()
    routes._rate_buckets.clear()
    runner.registry.clear()
    await store.init_db()
    yield
    # 清理可能残留的执行协程，避免跨测试泄漏
    for atask in list(runner.registry.values()):
        atask.cancel()


@pytest.fixture
async def client(api_env):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from nonebot_plugin_parser.api import routes

    app = FastAPI()
    app.include_router(routes.build_router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://svc.test") as c:
        yield c


def _install_stub(monkeypatch, tmp_path, *, behavior: str = "ok", hang_event=None):
    """注册 stub parser + stub renderer，预置真实媒体文件，返回 (parser, 文件路径表)。"""
    from nonebot_plugin_parser.exception import TipException
    from nonebot_plugin_parser.helper import UniHelper, UniMessage
    from nonebot_plugin_parser.matchers import KEYWORD_PARSER_MAP
    from nonebot_plugin_parser import renders
    from nonebot_plugin_parser.renders import BaseRenderer
    from nonebot_plugin_parser.parsers.base import BaseParser, Platform, PlatformEnum, handle
    from nonebot_plugin_parser.parsers.data import Author, ImageContent, ParseResult, VideoContent

    img = tmp_path / "img.png"
    img.write_bytes(_PNG)
    vid = tmp_path / "vid.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video-body")
    snd = tmp_path / "snd.mp3"
    snd.write_bytes(b"ID3fake-audio-body")
    avatar = tmp_path / "avatar.jpg"
    avatar.write_bytes(_JPEG)

    class StubParser(BaseParser):
        _abstract_parser = True

        platform: ClassVar[Platform] = Platform(name=PlatformEnum("douyin"), display_name="测试")

        @handle("stub", r"stub\.example/\w+")
        async def _parse(self, searched):
            if behavior == "tip":
                raise TipException("该作品已下架")
            if behavior == "error":
                raise RuntimeError(f"secret-host-path {tmp_path} leaked")
            if behavior == "hang" and hang_event is not None:
                await hang_event.wait()
            return ParseResult(
                platform=self.platform,
                author=Author(name="作者", avatar=avatar),
                title="标题",
                text="文案",
                timestamp=1700000000,
                url="https://stub.example/abc",
                contents=[VideoContent(vid, cover=avatar), ImageContent(img)],
            )

    class StubRenderer(BaseRenderer):
        async def render_messages(self, result):
            # video → file:// 形态; 内嵌一段 bytes 渲染切片 → base64 形态
            for cont in result.contents:
                if isinstance(cont, VideoContent):
                    yield UniMessage(UniHelper.video_seg(await cont.get_path()))
                    yield UniMessage(UniHelper.img_seg(_PNG))  # base64:// 形态
                elif isinstance(cont, ImageContent):
                    yield UniMessage(UniHelper.img_seg(await cont.get_path()))
            yield UniMessage(UniHelper.record_seg(snd))  # record → file:// 形态
            # 合并转发 Reference 节点（嵌套段，走递归改写）
            yield UniMessage(
                UniHelper.construct_forward_message([UniHelper.img_seg(img), "节点文本"], user_id="10000")
            )

    monkeypatch.setattr(renders, "_RENDERER_CACHE", {"douyin": StubRenderer()})
    parser = StubParser()
    monkeypatch.setitem(KEYWORD_PARSER_MAP, "stub", parser)
    return parser, {"img": img, "vid": vid, "snd": snd, "avatar": avatar}


async def _submit(client, url: str = "https://stub.example/abc"):
    return await client.post("/parser/api/tasks", json={"url": url}, headers=AUTH)


async def _wait_done(client, task_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/parser/api/tasks/{task_id}", headers=AUTH)
        body = r.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return r, body
        await asyncio.sleep(0.05)
    raise AssertionError(f"任务 {timeout}s 内未结束")


# ── 鉴权与限流 ───────────────────────────────────────────────────────


async def test_auth_missing_header_401(client):
    r = await client.post("/parser/api/tasks", json={"url": "https://stub.example/abc"})
    assert r.status_code == 401


async def test_auth_wrong_token_403(client):
    # 鉴权在 body/平台校验之前：任意 URL 均可探到 401/403
    r = await client.post("/parser/api/tasks", json={"url": "https://stub.example/abc"})
    assert r.status_code == 401
    r = await client.post(
        "/parser/api/tasks", json={"url": "https://stub.example/abc"}, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 403


async def test_auth_non_ascii_no_500_and_counted(client):
    """非 ASCII token / 签名：403 而非 500，且计入限流（bytes 比较不抛 TypeError）。

    header 按 latin-1 透传 UTF-8 字节，与 uvicorn 对原始字节的解码行为一致：
    服务端拿到的 token str 是非 ASCII 的 mojibake —— str 形态的 compare_digest
    会 TypeError（未捕获即 500 且绕过限流），bytes 形态安全返回 False。
    """
    bad_token = "测试token".encode("utf-8")
    bad_headers = {"Authorization": b"Bearer " + bad_token}
    for _ in range(3):
        r = await client.post("/parser/api/tasks", json={"url": "https://stub.example/abc"}, headers=bad_headers)
        assert r.status_code == 403, "非 ASCII token 应走 403 而非 500"
    # 媒体端点的 sig 同理：percent-decode 后是非 ASCII str → 403 而非 500
    r = await client.get(f"/parser/api/media/{'a' * 22}/x.jpg?exp=9999999999&sig=%E6%B5%8B%E8%AF%95")
    assert r.status_code == 403
    # 已被计入限流：再来 7 次普通失败即触发 429（共 10 次失败配额）
    for _ in range(7):
        r = await client.post(
            "/parser/api/tasks", json={"url": "https://stub.example/abc"}, headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 403
    r = await client.post(
        "/parser/api/tasks", json={"url": "https://stub.example/abc"}, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 429


async def test_auth_rate_limit_and_skip_token_compare(client, monkeypatch):
    """连续鉴权失败触发限流；被限后即使携带正确 token 也 429（先查桶再比对）。"""
    from nonebot_plugin_parser.api import routes

    for _ in range(10):
        r = await client.post(
            "/parser/api/tasks", json={"url": "https://stub.example/abc"}, headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 403
    r = await client.post(
        "/parser/api/tasks", json={"url": "https://stub.example/abc"}, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}
    # 被限的 IP 用正确 token 也被拒 —— 未消耗 token 比对
    r = await _submit(client)
    assert r.status_code == 429
    routes.prune_rate_buckets()


# ── 提交：422 / 429 / 判定顺序 / 去重 ────────────────────────────────


async def test_submit_rejects_non_http_scheme(client):
    r = await _submit(client, "ftp://stub.example/abc")
    assert r.status_code == 422


async def test_submit_rejects_unknown_platform(client):
    r = await _submit(client, "https://unknown-platform.example/xyz")
    assert r.status_code == 422


async def test_submit_202_envelope_headers(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    r = await _submit(client)
    assert r.status_code == 202
    body = r.json()
    assert re.fullmatch(r"[A-Za-z0-9_-]{8,64}", body["task_id"])
    assert body["status"] == "queued"
    assert r.headers["location"] == f"/parser/api/tasks/{body['task_id']}"
    assert r.headers["retry-after"] == "2"


async def test_dedup_normalizes_url(client, monkeypatch, tmp_path):
    """同 URL 去重：host 大小写 / fragment 差异不产生新任务。"""
    _install_stub(monkeypatch, tmp_path)
    r1 = await _submit(client, "https://stub.example/abc")
    id1 = r1.json()["task_id"]
    r2 = await _submit(client, "https://STUB.example/abc#fragment")
    assert r2.status_code == 202
    assert r2.json()["task_id"] == id1


async def test_queue_full_order_dedup_before_429(client, monkeypatch, tmp_path):
    """队列满时：同 URL 仍拿回既有 task_id，新 URL 才 429。"""
    _install_stub(monkeypatch, tmp_path)
    r1 = await _submit(client, "https://stub.example/abc")
    task_id = r1.json()["task_id"]

    from nonebot_plugin_parser.api import runner

    monkeypatch.setattr(runner, "_MAX_ACTIVE_TASKS", 0)
    r2 = await _submit(client, "https://stub.example/abc")
    assert r2.status_code == 202
    assert r2.json()["task_id"] == task_id
    r3 = await _submit(client, "https://stub.example/other")
    assert r3.status_code == 429


async def test_succeeded_url_resubmits_new_task(client, monkeypatch, tmp_path):
    """succeeded 后同 URL 再提交 → 新任务（去重仅覆盖 queued/running）。"""
    _install_stub(monkeypatch, tmp_path)
    r1 = await _submit(client)
    task_id = r1.json()["task_id"]
    await _wait_done(client, task_id)
    r2 = await _submit(client)
    assert r2.json()["task_id"] != task_id
    # 等第二个任务结束，避免残留协程
    await _wait_done(client, r2.json()["task_id"])


# ── 轮询信封 / 现签 / 落库无泄露 ─────────────────────────────────────


async def test_succeeded_envelope_snapshot_and_signing(client, monkeypatch, tmp_path):
    """成功信封字段全集快照；媒体 URL 现签；DB result_json 零 URL 零签名零宿主路径。"""
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import store

    r = await _submit(client)
    task_id = r.json()["task_id"]
    r, body = await _wait_done(client, task_id)
    assert body["status"] == "succeeded"
    assert body["platform"] == "douyin"
    assert set(body.keys()) == {
        "task_id",
        "status",
        "url",
        "platform",
        "source",
        "error_kind",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "result",
    }
    result = body["result"]
    assert set(result.keys()) == {"metadata", "messages"}
    meta = result["metadata"]
    assert meta["title"] == "标题"
    assert meta["author"]["name"] == "作者"
    assert meta["author"]["avatar"].startswith("http://media.test/parser/api/media/")
    assert "sig=" in meta["author"]["avatar"] and "exp=" in meta["author"]["avatar"]

    # messages：全部媒体引用都是现签 URL，无 base64 / file:// / 宿主路径残留
    text = json.dumps(result, ensure_ascii=False)
    assert "base64://" not in text
    assert "file://" not in text
    assert str(tmp_path) not in text
    assert text.count("http://media.test/parser/api/media/") >= 1

    # metadata 覆盖 cover 改写
    assert meta["contents"][0]["cover"].startswith("http://media.test/parser/api/media/")

    # 落库层无泄露：result_json 不含 base_url、签名、宿主路径（源链接 url 属合法载荷）
    row = await store.get_task(task_id)
    raw = sqlite3.connect(str(tmp_path / "api_tasks.db")).execute(
        "SELECT result_json FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    assert "http://media.test" not in raw
    assert "sig=" not in raw
    assert "exp=" not in raw
    assert "file://" not in raw
    assert str(tmp_path) not in raw
    assert raw.count("media://") >= 1
    assert '"url": "https://stub.example/abc"' in raw
    assert row is not None and row["staged_files"]


async def test_repeat_get_fresh_signatures(client, monkeypatch, tmp_path):
    """重复 GET 均返回有效现签 URL（晚取晚发不过期）。"""
    _install_stub(monkeypatch, tmp_path)
    task_id = (await _submit(client)).json()["task_id"]
    _, body1 = await _wait_done(client, task_id)
    url1 = body1["result"]["metadata"]["contents"][0]["file"]
    await asyncio.sleep(1.1)  # 让 exp 至少跨 1s
    r2 = await client.get(f"/parser/api/tasks/{task_id}", headers=AUTH)
    body2 = r2.json()
    url2 = body2["result"]["metadata"]["contents"][0]["file"]
    assert url1 != url2  # exp 变化 → URL 现签刷新

    # 两个 URL 都能拉到媒体
    for url in (url1, url2):
        mr = await client.get(url)
        assert mr.status_code == 200


async def test_error_masking(client, monkeypatch, tmp_path):
    """tip 文案透传；通用异常只给固定文案，宿主路径不外泄。"""
    from nonebot_plugin_parser.config import pconfig

    # 关掉解析层即时重试与退避，避免本用例白等 7s
    monkeypatch.setattr(pconfig, "parser_parse_retry_max", 0)
    monkeypatch.setattr(pconfig, "parser_parse_retry_delay", 0.0)
    _install_stub(monkeypatch, tmp_path, behavior="tip")
    task_id = (await _submit(client)).json()["task_id"]
    _, body = await _wait_done(client, task_id)
    assert body["status"] == "failed"
    assert body["error_kind"] == "tip"
    assert body["error"] == "该作品已下架"

    _install_stub(monkeypatch, tmp_path, behavior="error")
    task_id = (await _submit(client)).json()["task_id"]
    _, body = await _wait_done(client, task_id)
    assert body["error_kind"] == "parse_error"
    assert body["error"] == "解析失败"
    assert "secret-host-path" not in json.dumps(body)
    assert str(tmp_path) not in json.dumps(body)


# ── 段导出形态 / 转存 / 扩展名 ───────────────────────────────────────


async def test_segment_forms_staged_with_correct_ext(client, monkeypatch, tmp_path):
    """video(file://)、渲染切片(base64://→魔数 .png)、record(file://)、forward 嵌套段全部转存。"""
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import media, store

    task_id = (await _submit(client)).json()["task_id"]
    _, body = await _wait_done(client, task_id)
    assert body["status"] == "succeeded"

    # 段类型覆盖：video/image/record/node
    seg_types = {seg["type"] for msg in body["result"]["messages"] for seg in msg}
    assert {"video", "image", "record", "node"} <= seg_types
    # base64 渲染切片按魔数落成 .png；mp4 视频 / jpg 头像封面均在列
    row = await store.get_task(task_id)
    row_files = [f["filename"] for f in row["staged_files"]]
    assert any(f.endswith(".png") for f in row_files)
    assert any(f.endswith(".mp4") for f in row_files)
    assert any(f.endswith(".jpg") for f in row_files)
    # 每个转存文件都真实存在
    for f in row_files:
        assert (media.MEDIA_ROOT / task_id / f).is_file()


async def test_ext_whitelist_falls_back_to_bin(client, monkeypatch, tmp_path):
    from nonebot_plugin_parser.api.media import gen_filename, pick_ext, pick_ext_from_magic

    assert pick_ext("photo.JPG") == ".jpg"
    assert pick_ext("video.mp4") == ".mp4"
    assert pick_ext("archive.zip") == ".bin"
    assert pick_ext("noext") == ".bin"
    assert pick_ext(None) == ".bin"
    assert gen_filename(".zip").endswith(".bin")
    assert pick_ext_from_magic(_PNG) == ".png"
    assert pick_ext_from_magic(_JPEG) == ".jpg"
    assert pick_ext_from_magic(b"\x00\x00\x00\x18ftypmp42") == ".mp4"
    assert pick_ext_from_magic(b"whatever") == ".bin"


# ── 媒体端点：穿越 / 签名 / Range / nosniff ─────────────────────────


async def _staged_media_url(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import routes

    task_id = (await _submit(client)).json()["task_id"]
    _, body = await _wait_done(client, task_id)
    url = body["result"]["metadata"]["contents"][0]["file"]
    return task_id, url


async def test_media_endpoint_happy_path(client, monkeypatch, tmp_path):
    task_id, url = await _staged_media_url(client, monkeypatch, tmp_path)
    r = await client.get(url)
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("video/")
    # Range 分片（协议端拉大视频需要 206）
    r2 = await client.get(url, headers={"Range": "bytes=0-3"})
    assert r2.status_code == 206
    assert r2.content == b"\x00\x00\x00\x18"


async def test_media_traversal_probes(client, monkeypatch, tmp_path):
    """task_id / filename 注入全被格式校验拦在签名与文件系统之前（404）。"""
    probes_task = ["../../etc", "..%2Fescape", "short", "has space!!", "a" * 100]
    for tid in probes_task:
        r = await client.get(f"/parser/api/media/{quote(tid, safe='')}/x.jpg?exp=9999999999&sig=deadbeef")
        assert r.status_code == 404, f"task_id={tid}"
    probes_file = ["../secret.jpg", "a/b.jpg", "..\\win.jpg", "x.jpg%00", "x.unknown"]
    for fn in probes_file:
        r = await client.get(f"/parser/api/media/{'a' * 22}/{quote(fn, safe='')}?exp=9999999999&sig=deadbeef")
        assert r.status_code == 404, f"filename={fn}"


async def test_media_signature_expiry_and_tamper(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import routes

    task_id = (await _submit(client)).json()["task_id"]
    _, body = await _wait_done(client, task_id)
    filename = body["result"]["metadata"]["contents"][0]["file"].rsplit("/", 1)[-1].split("?")[0]

    # 有效签名但文件不存在 → 404（签名对 ≠ 文件在）
    url = routes._media_url(task_id, "nonexistent.mp4")
    r = await client.get(url)
    assert r.status_code == 404

    # 过期 → 410
    exp = int(time.time()) - 10
    sig = routes._sign(task_id, filename, exp)
    r = await client.get(f"/parser/api/media/{task_id}/{filename}?exp={exp}&sig={sig}")
    assert r.status_code == 410

    # 篡改 → 403
    exp = int(time.time()) + 3600
    r = await client.get(f"/parser/api/media/{task_id}/{filename}?exp={exp}&sig={'0' * 32}")
    assert r.status_code == 403

    # 缺签名参数 → 422（fastapi 必填校验）
    r = await client.get(f"/parser/api/media/{task_id}/{filename}")
    assert r.status_code == 422


# ── DELETE 语义 ──────────────────────────────────────────────────────


async def test_delete_running_cancels_and_releases(client, monkeypatch, tmp_path):
    """DELETE running 任务：await 取消 + cancelled 状态 + 配额释放；可重新提交同 URL。"""
    hang = asyncio.Event()
    _install_stub(monkeypatch, tmp_path, behavior="hang", hang_event=hang)
    from nonebot_plugin_parser.api import media, runner, store

    task_id = (await _submit(client)).json()["task_id"]
    await asyncio.sleep(0.2)  # 等 runner 进入 hang
    assert task_id in runner.registry

    r = await client.delete(f"/parser/api/tasks/{task_id}", headers=AUTH)
    assert r.status_code == 204
    body = (await client.get(f"/parser/api/tasks/{task_id}", headers=AUTH)).json()
    assert body["status"] == "cancelled"
    assert body["error_kind"] == "cancelled"
    assert task_id not in runner.registry

    # DELETE 返回即可立即重新提交同 URL（与旧任务收尾串行，无交错）
    r2 = await _submit(client)
    assert r2.status_code == 202
    assert r2.json()["task_id"] != task_id
    await client.delete(f"/parser/api/tasks/{r2.json()['task_id']}", headers=AUTH)
    hang.set()


async def test_delete_terminal_task_removes_row_and_dir(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import media

    task_id = (await _submit(client)).json()["task_id"]
    await _wait_done(client, task_id)
    assert (media.MEDIA_ROOT / task_id).is_dir()

    r = await client.delete(f"/parser/api/tasks/{task_id}", headers=AUTH)
    assert r.status_code == 204
    assert (await client.get(f"/parser/api/tasks/{task_id}", headers=AUTH)).status_code == 404
    assert not (media.MEDIA_ROOT / task_id).exists()


async def test_delete_unknown_404(client):
    r = await client.delete(f"/parser/api/tasks/{'a' * 22}", headers=AUTH)
    assert r.status_code == 404


# ── 配额全生命周期 ───────────────────────────────────────────────────


async def test_quota_reserve_release_and_rebuild(tmp_path, monkeypatch):
    from nonebot_plugin_parser.api import media
    from nonebot_plugin_parser.config import pconfig

    monkeypatch.setattr(pconfig, "parser_api_media_quota_mb", 1)
    media.quota.rebuild()
    await media.quota.reserve(500)
    await media.quota.reserve(500)
    with pytest.raises(media.MediaQuotaExceeded):
        await media.quota.reserve(1024 * 1024)
    media.quota.release(1000)
    assert media.quota.used == 0

    # rebuild 从磁盘实际大小重建
    d = media.MEDIA_ROOT / ("t" * 8)
    d.mkdir(parents=True)
    (d / "f.bin").write_bytes(b"x" * 2048)
    assert media.quota.rebuild() == 2048


async def test_quota_exceeded_fails_task_and_cleans(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import media

    media.quota.rebuild()
    media.quota._used = media.quota.limit  # 打满配额
    task_id = (await _submit(client)).json()["task_id"]
    _, body = await _wait_done(client, task_id)
    assert body["status"] == "failed"
    assert body["error_kind"] == "quota"
    assert body["error"] == "媒体转存配额超限"
    assert not (media.MEDIA_ROOT / task_id).exists()  # 部分转存已清理


async def test_stage_path_missing_source_no_quota_leak(tmp_path):
    from nonebot_plugin_parser.api import media

    media.quota.rebuild()
    with pytest.raises(FileNotFoundError):
        await media.stage_path(tmp_path / "ghost.bin", tmp_path)
    assert media.quota.used == 0


# ── TTL 清理 / stale 标记（顺序敏感） ────────────────────────────────


def _age_rows(tmp_path, task_id, *, created_shift: int, finished_shift: int | None = None):
    conn = sqlite3.connect(str(tmp_path / "api_tasks.db"))
    now = int(time.time())
    sets = [f"created_at = {now - created_shift}"]
    if finished_shift is not None:
        sets.append(f"finished_at = {now - finished_shift}")
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


async def test_cleanup_expired_rows_and_orphan_dirs(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import media, run_cleanup
    from nonebot_plugin_parser.api import store

    monkeypatch.setattr(media.quota, "_used", 0)

    task_id = (await _submit(client)).json()["task_id"]
    await _wait_done(client, task_id)
    _age_rows(tmp_path, task_id, created_shift=7200, finished_shift=7200)  # 终态超 TTL

    # 长期 queued 行（无执行协程）也按 created_at 过期
    queued_id = "q" * 22
    await store.create_task(queued_id, "https://stub.example/q", "https://stub.example/q")
    _age_rows(tmp_path, queued_id, created_shift=7200)

    # 无主目录：陈旧的被清，新鲜的（mtime 宽限）保留
    stale_orphan = media.MEDIA_ROOT / ("z" * 22)
    fresh_orphan = media.MEDIA_ROOT / ("y" * 22)
    for d in (stale_orphan, fresh_orphan):
        d.mkdir(parents=True)
        (d / "f.bin").write_bytes(b"data")
    old = time.time() - 3600
    import os

    os.utime(stale_orphan, (old, old))

    await run_cleanup()

    assert (await store.get_task(task_id)) is None
    assert (await store.get_task(queued_id)) is None
    assert not stale_orphan.exists()
    assert fresh_orphan.exists()  # mtime 宽限内不动


async def test_stale_marked_before_cleanup_can_delete(client, monkeypatch, tmp_path):
    """stale 标记先于清理：interrupted 行拿到 finished_at，不会被清理误删。"""
    from nonebot_plugin_parser.api import store

    task_id = "s" * 22
    await store.create_task(task_id, "https://stub.example/s", "https://stub.example/s")
    await store.set_running_if_queued(task_id, "douyin")
    _age_rows(tmp_path, task_id, created_shift=7200)  # 已超 TTL 的残留 running

    await store.mark_stale_interrupted()  # startup 第一步
    row = await store.get_task(task_id)
    assert row["status"] == "failed"
    assert row["error_kind"] == "interrupted"

    expired = await store.cleanup_expired()  # 清理 job 首跑
    assert all(r["task_id"] != task_id for r in expired)
    assert (await store.get_task(task_id))["status"] == "failed"


async def test_shutdown_cancels_pending(client, monkeypatch, tmp_path):
    hang = asyncio.Event()
    _install_stub(monkeypatch, tmp_path, behavior="hang", hang_event=hang)
    from nonebot_plugin_parser.api import runner, store

    task_id = (await _submit(client)).json()["task_id"]
    await asyncio.sleep(0.2)
    await runner.shutdown()
    row = await store.get_task(task_id)
    assert row["status"] == "cancelled"
    assert not runner.registry
    hang.set()


async def test_cancel_pending_downloads_cancels_tasks(tmp_path):
    """cancel_pending_downloads 直接取消 Task 形态的惰性下载字段（回归：不能只遍历裸协程）。"""
    from asyncio import Task

    from nonebot_plugin_parser.parsers.data import (
        Author,
        ImageContent,
        ParseResult,
        Platform,
        VideoContent,
    )
    from nonebot_plugin_parser.parsers.base import PlatformEnum

    async def _sleep():
        await asyncio.sleep(60)

    pending = asyncio.ensure_future(_sleep())
    cover = asyncio.ensure_future(_sleep())
    avatar = asyncio.ensure_future(_sleep())

    platform = Platform(name=PlatformEnum("douyin"), display_name="测试")
    result = ParseResult(
        platform=platform,
        author=Author(name="作者", avatar=avatar),
        cover_image=cover,
        contents=[VideoContent(pending, cover=cover), ImageContent(pending)],
    )
    result.cancel_pending_downloads()
    # 让 cancel 落地
    await asyncio.sleep(0)
    for t in (pending, cover, avatar):
        assert isinstance(t, Task) and t.cancelled()


# ── store 并发与写前复查 ─────────────────────────────────────────────


async def test_store_concurrent_writes(tmp_path, monkeypatch):
    """to_thread 多协程并发读写不炸（每操作新建连接 + WAL）。"""
    from nonebot_plugin_parser.api import store

    ids = [f"c{i:02d}{'x' * 16}" for i in range(20)]
    await asyncio.gather(*[store.create_task(i, "https://stub.example/x", f"https://stub.example/{i}") for i in ids])
    await asyncio.gather(*[store.set_running_if_queued(i, "douyin") for i in ids])
    rows = await asyncio.gather(*[store.get_task(i) for i in ids])
    assert all(r["status"] == "running" for r in rows)


async def test_set_result_conditional_on_running(tmp_path, monkeypatch):
    """DELETE 竞态防线：行不在 running 态时 set_result 拒写。"""
    from nonebot_plugin_parser.api import store

    task_id = "r" * 22
    await store.create_task(task_id, "https://stub.example/x", "https://stub.example/x")
    # 行仍 queued → 拒写
    assert not await store.set_result(task_id, {"metadata": {}, "messages": []}, [], 0)
    await store.set_running_if_queued(task_id, "douyin")
    assert await store.set_result(task_id, {"metadata": {}, "messages": []}, [{"filename": "a.png", "size": 3}], 3)
    # 已 succeeded → 再写被条件拦截
    assert not await store.set_result(task_id, {"metadata": {}, "messages": []}, [], 0)


async def test_list_tasks_without_result_column(client, monkeypatch, tmp_path):
    _install_stub(monkeypatch, tmp_path)
    for _ in range(3):
        r = await _submit(client)
        await _wait_done(client, r.json()["task_id"])
    r = await client.get("/parser/api/tasks?limit=2&offset=1", headers=AUTH)
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    # 列表项走信封字段（不含 result），DB 大列（result_json/staged_files）不 SELECT
    expected_keys = {
        "task_id", "status", "url", "platform", "source", "error_kind", "error",
        "created_at", "started_at", "finished_at",
    }
    assert all(set(item.keys()) == expected_keys for item in body["items"])


# ── 聊天来源任务记录（方案 1：只记状态+失败原因） ────────────────────


async def test_chat_task_lifecycle_and_reason():
    """聊天任务：running 行 → 成功置 succeeded / 失败置 failed 带原因。"""
    from nonebot_plugin_parser.api import store
    from nonebot_plugin_parser.api.chat import record_chat_result, record_chat_start

    task_id = await record_chat_start("https://stub.example/Chat", "douyin")
    assert task_id is not None
    row = await store.get_task(task_id)
    assert row["source"] == "chat"
    assert row["status"] == "running"
    assert row["platform"] == "douyin"
    assert row["started_at"] is not None

    await record_chat_result(task_id, ok=False, error_kind="parse_error", error="ParseException: 风控空 body")
    row = await store.get_task(task_id)
    assert row["status"] == "failed"
    assert row["error_kind"] == "parse_error"
    assert row["error"] == "ParseException: 风控空 body"

    # 成功路径：无结果载荷的 succeeded
    ok_id = await record_chat_start("https://stub.example/ok", "douyin")
    await record_chat_result(ok_id, ok=True)
    row = await store.get_task(ok_id)
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None


async def test_chat_record_never_raises(tmp_path, monkeypatch):
    """记录入口整体吞异常：DB 指向父目录不存在的位置也不拖垮聊天链路。"""
    from nonebot_plugin_parser.api import store
    from nonebot_plugin_parser.api.chat import record_chat_result, record_chat_start

    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "no-such-dir" / "x.db")
    assert await record_chat_start("https://stub.example/x", "douyin") is None
    await record_chat_result("whatever", ok=False, error_kind="parse_error", error="x")


async def test_url_filter_normalizes(client, monkeypatch, tmp_path):
    """?url= 按规范化 URL 直查（host 大小写/fragment 不影响命中）。"""
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api.chat import record_chat_start

    r = await _submit(client, "https://stub.example/target")
    api_id = r.json()["task_id"]
    chat_id = await record_chat_start("https://stub.example/other", "bilibili")

    # 大小写 + fragment 变体命中同一 url_norm；列表与直查都返回该任务
    for variant in ("https://STUB.example/target#frag", "https://stub.example/target"):
        body = (await client.get("/parser/api/tasks", params={"url": variant}, headers=AUTH)).json()
        assert body["total"] == 1
        assert body["items"][0]["task_id"] == api_id
        assert body["items"][0]["source"] == "api"
    # 聊天来源的链接也能查到
    body = (await client.get("/parser/api/tasks", params={"url": "https://stub.example/other"}, headers=AUTH)).json()
    assert body["total"] == 1
    assert body["items"][0]["task_id"] == chat_id
    assert body["items"][0]["source"] == "chat"
    # 查不到 → 空列表而非 404
    body = (await client.get("/parser/api/tasks", params={"url": "https://stub.example/none"}, headers=AUTH)).json()
    assert body == {"total": 0, "limit": 50, "offset": 0, "items": []}


async def test_dedup_ignores_chat_rows(client, monkeypatch, tmp_path):
    """跨源去重隔离：聊天 running 行不参与 API 去重（否则调用方轮询不到结果载荷）。"""
    _install_stub(monkeypatch, tmp_path)
    from nonebot_plugin_parser.api import store
    from nonebot_plugin_parser.api.chat import record_chat_start

    chat_id = await record_chat_start("https://stub.example/dup", "douyin")
    r = await _submit(client, "https://stub.example/dup")
    body = r.json()
    assert body["task_id"] != chat_id
    row = await store.get_task(body["task_id"])
    assert row["source"] == "api"


async def test_schema_migration_adds_source(tmp_path, monkeypatch):
    """v2.7.21 旧库（无 source 列）平滑迁移：补列 + 存量行默认 api。"""
    import sqlite3

    from nonebot_plugin_parser.api import store

    db = tmp_path / "old_api_tasks.db"
    monkeypatch.setattr(store, "_DB_PATH", db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, url TEXT NOT NULL, url_norm TEXT NOT NULL, "
        "platform TEXT, status TEXT NOT NULL, error_kind TEXT, error TEXT, result_json TEXT, "
        "staged_files TEXT NOT NULL DEFAULT '[]', staged_bytes INTEGER NOT NULL DEFAULT 0, "
        "created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO tasks (task_id, url, url_norm, status, created_at) "
        "VALUES (?, ?, ?, 'succeeded', 1700000000)",
        ("o" * 22, "https://stub.example/old", "https://stub.example/old"),
    )
    conn.commit()
    conn.close()

    await store.init_db()
    row = await store.get_task("o" * 22)
    assert row["source"] == "api"
    # 迁移后新写入不受影响
    await store.create_task("n" * 22, "https://stub.example/new", "https://stub.example/new", source="chat")
    assert (await store.get_task("n" * 22))["source"] == "chat"
