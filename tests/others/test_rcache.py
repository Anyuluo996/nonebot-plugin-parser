"""跨机解析结果缓存测试：五步流水线、验签绑定、熔断、跨机拉取与回写。

条目是半可信输入的完整攻击面回归：zlib 炸弹、路径穿越文件名、条目复制他键、
续命、验签失败不删键、结构违规删键、CAS 回写竞态、EXDEV 回退、deadline 超时。

redis 用内存 fake 桩（dict + TTL 语义），不依赖真实 redis 进程。

注意: nonebot_plugin_parser.* 的 import 必须放在测试函数内部,
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import json
import time
import asyncio
import hashlib

import pytest

_URL_NORM = "https://example.com/watch/1"
_TASK_ID = "a" * 22
_FILENAME = "b" * 22 + ".jpg"
_STAGED = [{"filename": _FILENAME, "size": 10}]
_PAYLOAD = {"metadata": {"title": "t"}, "messages": [[{"type": "text", "data": {"text": "x"}}]]}


class FakeRedis:
    """内存 redis 桩：get/set/setex/delete/eval(CAS Lua 语义)。"""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, keepttl=False):
        # 生产 redis GET 恒返回 bytes：str 写入也编码，保持桩与生产行为一致
        if isinstance(value, str):
            value = value.encode()
        if keepttl and key in self.store:
            self.store[key] = value  # KEEPTTL: 保留剩余寿命
            return True
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                self.ttl.pop(k, None)
                n += 1
        return n

    async def eval(self, script, numkeys, key, *args):
        # 仅支持 CAS 回写脚本语义：ARGV[0]=期望 sha1, ARGV[1]=新值
        import hashlib

        cur = self.store.get(key)
        if cur is None:
            return 0
        if hashlib.sha1(cur).hexdigest() != args[0]:
            return 0
        self.store[key] = args[1]
        return 1

    async def ping(self):
        return True


@pytest.fixture
def rcache_env(tmp_path, monkeypatch):
    """隔离环境：fake redis + 独立 MEDIA_ROOT + 确定性 instance_id。"""
    from nonebot_plugin_parser.api import media, rcache
    from nonebot_plugin_parser.config import pconfig

    fake = FakeRedis()
    monkeypatch.setattr(rcache, "_get_client", lambda: fake)
    monkeypatch.setattr(rcache, "_client", fake)
    monkeypatch.setattr(media, "MEDIA_ROOT", tmp_path / "api_media")
    monkeypatch.setattr(pconfig, "parser_rcache_enabled", True)
    monkeypatch.setattr(pconfig, "parser_rcache_instance_id", "host-a")
    monkeypatch.setattr(
        pconfig, "parser_rcache_peer_map", {"host-a": "http://127.0.0.1:5", "host-b": "http://127.0.0.1:6"}
    )
    monkeypatch.setattr(pconfig, "parser_rcache_skip_keywords", [])
    monkeypatch.setattr(rcache, "_fail_until", 0.0)
    rcache.reset_keys()
    rcache.reset_metrics()
    # nonce 文件隔离
    monkeypatch.setattr(rcache, "_nonce_file", lambda: tmp_path / "rcache_instance.json")
    return fake


def _make_entry(
    fake: FakeRedis, url_norm: str = _URL_NORM, host: str = "host-a", *, bad_sig: bool = False, **overrides
):
    """直接构造签名条目写入 fake redis（绕过 put，便于控制字段）。

    ``bad_sig``：签名替换为全零（模拟外来/损坏写入，验签必失败）。
    """
    from nonebot_plugin_parser.api import rcache

    key = rcache.make_key(url_norm)
    entry = {
        "v": 1,
        "host": host,
        "origin_task_id": _TASK_ID,
        "platform": "test",
        "url_key": rcache.url_key(url_norm),
        "cfg_fp": rcache.cfg_fingerprint(),
        "created_at": int(time.time()),
        "payload": _PAYLOAD,
        "staged_files": _STAGED,
        "staged_bytes": 10,
    }
    entry.update(overrides)
    entry.pop("sig", None)
    if bad_sig:
        entry["sig"] = "0" * 32
        fake.store[key] = b"R" + rcache._canonical(entry)
    else:
        blob = rcache._encode_entry(entry)
        fake.store[key] = blob
    fake.ttl[key] = rcache.eff_ttl()
    return key


async def test_roundtrip_and_local_reuse(rcache_env, tmp_path):
    from nonebot_plugin_parser.api import rcache

    """本机条目：硬链复用进新任务目录，文件名与载荷保真。"""
    from nonebot_plugin_parser.api import media

    # 源任务目录与文件
    src = media.task_media_dir(_TASK_ID) / _FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"0123456789")

    fake = rcache_env
    _make_entry(fake)
    new_id = "c" * 22
    got = await rcache.get(_URL_NORM, new_id)
    assert got is not None
    payload, staged, total = got
    assert payload == _PAYLOAD
    assert staged == _STAGED
    assert total == 10
    dest = media.task_media_dir(new_id) / _FILENAME
    assert dest.is_file() and dest.read_bytes() == b"0123456789"
    assert rcache.metrics["hit_local"] == 1


async def test_local_source_missing_deletes_key(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """本机条目源文件缺失 → 删键自愈 + 未命中。"""
    fake = rcache_env
    key = _make_entry(fake)
    got = await rcache.get(_URL_NORM, "d" * 22)
    assert got is None
    assert key not in fake.store


async def test_copy_to_other_key_fails_verify_then_deleted(rcache_env):
    """条目被整条复制到其它链接的键下：url_key 绑定校验 → 删键。"""
    from nonebot_plugin_parser.api import rcache

    fake = rcache_env
    key = _make_entry(fake)
    blob = fake.store[key]
    other_norm = "https://example.com/watch/2"
    other_key = key.replace(rcache.url_key(_URL_NORM), rcache.url_key(other_norm))
    fake.store[other_key] = blob  # 复制（sig 对原条目有效，但 url_key 不匹配新键）
    got = await rcache.get(other_norm, "e" * 22)
    assert got is None
    assert other_key not in fake.store, "已验签但结构违规应删键"


async def test_sig_failure_keeps_key(rcache_env):
    """sig 验签失败（外来写入）→ 未命中但**不删键**。"""
    from nonebot_plugin_parser.api import rcache

    fake = rcache_env
    key = _make_entry(fake, bad_sig=True)
    got = await rcache.get(_URL_NORM, "f" * 22)
    assert got is None
    assert key in fake.store, "验签失败不删键"


async def test_version_mismatch_keeps_key(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """v=2（升级期新版本写入）→ 未命中不删键。"""
    fake = rcache_env
    key = _make_entry(fake, v=2)
    got = await rcache.get(_URL_NORM, "0" * 22)
    assert got is None
    assert key in fake.store


async def test_cfg_fp_mismatch_keeps_key(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """cfg_fp 不同（另一台写入的异配置条目）→ 未命中不删键。"""
    fake = rcache_env
    key = _make_entry(fake, cfg_fp="deadbeef")
    got = await rcache.get(_URL_NORM, "1" * 22)
    assert got is None
    assert key in fake.store


async def test_created_at_renewal_guard(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """created_at 过旧（SETEX 续命攻击）→ 未命中。"""
    fake = rcache_env
    _make_entry(fake, created_at=int(time.time()) - rcache.eff_ttl() - 10)
    got = await rcache.get(_URL_NORM, "2" * 22)
    assert got is None


@pytest.mark.parametrize("bad_name", ["../../etc/passwd.jpg", "short.jpg", "x" * 22 + ".exe"])
async def test_malicious_filename_deleted(rcache_env, bad_name):
    from nonebot_plugin_parser.api import rcache

    """路径穿越/异形文件名（已验签的结构违规）→ 删键。"""
    fake = rcache_env
    key = _make_entry(fake, staged_files=[{"filename": bad_name, "size": 10}], staged_bytes=10)
    got = await rcache.get(_URL_NORM, "3" * 22)
    assert got is None
    assert key not in fake.store


async def test_zlib_bomb_deleted(rcache_env):
    """zlib 炸弹（解压超限，未验签垃圾）→ 删键。"""
    from nonebot_plugin_parser.api import rcache

    fake = rcache_env
    key = rcache.make_key(_URL_NORM)
    bomb = b"Z" + zlib_compress(b"0" * (rcache._MAX_ENTRY_BYTES * 4))
    fake.store[key] = bomb
    got = await rcache.get(_URL_NORM, "4" * 22)
    assert got is None
    assert key not in fake.store


def zlib_compress(data: bytes) -> bytes:
    import zlib

    return zlib.compress(data)


async def test_corrupt_json_deleted(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """JSON 损坏 → 删键。"""
    fake = rcache_env
    key = rcache.make_key(_URL_NORM)
    fake.store[key] = b"R{not-json"
    got = await rcache.get(_URL_NORM, "5" * 22)
    assert got is None
    assert key not in fake.store


async def test_staged_files_over_limit_deleted(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """staged_files 超过 500 项 → 结构违规删键。"""
    fake = rcache_env
    many = [{"filename": f"{i:022d}.jpg", "size": 0} for i in range(501)]
    key = _make_entry(fake, staged_files=many, staged_bytes=0)
    got = await rcache.get(_URL_NORM, "6" * 22)
    assert got is None
    assert key not in fake.store


async def test_breaker_skips_and_recovers(rcache_env, monkeypatch):
    """熔断期 get/put no-op；熔断窗口过后恢复。"""
    from nonebot_plugin_parser.api import rcache

    fake = rcache_env
    _make_entry(fake)
    rcache._trip_breaker()
    got = await rcache.get(_URL_NORM, "7" * 22)
    assert got is None
    assert rcache.metrics["skip_breaker"] == 1
    # 熔断过期恢复
    rcache._fail_until = 0.0
    got = await rcache.get(_URL_NORM, "8" * 22)
    assert got is None  # 源文件不存在 → 未命中，但已走出熔断


async def test_pool_full_is_miss_not_breaker(rcache_env, monkeypatch):
    """连接池满 → 当次未命中，不触发 30s 熔断。"""
    from nonebot_plugin_parser.api import rcache

    class PoolFullRedis(FakeRedis):
        async def get(self, key):
            raise RuntimeError("Too many connections")

    rcache._client = PoolFullRedis()
    monkeypatch.setattr(rcache, "_get_client", lambda: rcache._client)
    got = await rcache.get(_URL_NORM, "9" * 22)
    assert got is None
    assert not rcache._breaker_active(), "池满不应触发熔断"


async def test_put_skips_degraded_and_skip_keywords(rcache_env, monkeypatch):
    """降级产物与 skip_keywords 命中 → 不写缓存。"""
    from nonebot_plugin_parser.api import rcache
    from nonebot_plugin_parser.config import pconfig

    fake = rcache_env
    await rcache.put(_URL_NORM, _PAYLOAD, _STAGED, 10, _TASK_ID, "bm", degraded=True)
    assert not fake.store
    monkeypatch.setattr(pconfig, "parser_rcache_skip_keywords", ["ym"])
    await rcache.put(_URL_NORM, _PAYLOAD, _STAGED, 10, _TASK_ID, "ym")
    assert not fake.store
    await rcache.put(_URL_NORM, _PAYLOAD, _STAGED, 10, _TASK_ID, "bm")
    key = rcache.make_key(_URL_NORM)
    assert key in fake.store


async def test_put_oversize_skipped(rcache_env, monkeypatch):
    """超 1MB 的条目跳过写入。"""
    from nonebot_plugin_parser.api import rcache

    fake = rcache_env
    big_payload = {"metadata": {"t": "x" * (rcache._MAX_ENTRY_BYTES + 1)}, "messages": []}
    await rcache.put(_URL_NORM, big_payload, [], 0, _TASK_ID, "bm")
    assert not fake.store


async def test_peer_pull_with_mocked_stream(rcache_env, monkeypatch, tmp_path):
    from nonebot_plugin_parser.api import rcache

    """对端条目：mock direct_client 流式拉取 → 文件落地 + CAS 回写为本机条目 →
    二次命中走本机硬链。"""
    from nonebot_plugin_parser.api import media

    fake = rcache_env
    peer_entry_key = _make_entry(fake, host="host-b", origin_task_id="b" * 22)

    media_bytes = b"0123456789"  # 与条目声明 size=10 一致（限长校验按声明值对账）

    class FakeStreamResp:
        status_code = 200
        headers = {"content-length": str(len(media_bytes))}

        def aiter_bytes(self):
            async def _gen():
                yield media_bytes

            return _gen()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeDirectClient:
        def stream(self, method, url, **kw):
            assert kw.get("follow_redirects") is False
            assert "exp=" in url and "sig=" in url
            return FakeStreamResp()

    class FakeDownloader:
        direct_client = FakeDirectClient()

    import nonebot_plugin_parser.download as dl_mod

    monkeypatch.setattr(dl_mod, "DOWNLOADER", FakeDownloader())

    # 对端条目 origin_task_id="b"*22 的源文件在对端——本机当然没有, 走拉取
    new_id = "a" * 22
    got = await rcache.get(_URL_NORM, new_id)
    assert got is not None, "跨机拉取应成功"
    payload, staged, total = got
    assert staged == _STAGED
    dest = media.task_media_dir(new_id) / _FILENAME
    assert dest.read_bytes() == media_bytes
    assert rcache.metrics["hit_pull"] == 1

    # 回写为本机条目（origin_task_id 换成 new_id）
    rewritten = fake.store[peer_entry_key]
    decoded = rcache._decode_entry(rewritten)
    assert decoded is not None, "回写条目必须能通过验签"
    assert decoded["host"] == "host-a"
    assert decoded["origin_task_id"] == new_id

    # 二次命中 → 本机硬链
    rcache.reset_metrics()
    got2 = await rcache.get(_URL_NORM, "d" * 22)
    assert got2 is not None
    assert rcache.metrics["hit_local"] == 1


async def test_peer_pull_404_rolls_back(rcache_env, monkeypatch):
    from nonebot_plugin_parser.api import rcache

    """对端 404 → 整体未命中，已拉取文件回滚（目录被清）。"""
    from nonebot_plugin_parser.api import media

    fake = rcache_env
    _make_entry(
        fake,
        host="host-b",
        staged_files=[{"filename": _FILENAME, "size": 3}, {"filename": "c" * 22 + ".png", "size": 5}],
        staged_bytes=8,
    )

    class FakeStreamResp:
        status_code = 404
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeDirectClient:
        def stream(self, method, url, **kw):
            return FakeStreamResp()

    class FakeDownloader:
        direct_client = FakeDirectClient()

    import nonebot_plugin_parser.download as dl_mod

    monkeypatch.setattr(dl_mod, "DOWNLOADER", FakeDownloader())

    new_id = "e" * 22
    got = await rcache.get(_URL_NORM, new_id)
    assert got is None
    assert not (media.MEDIA_ROOT / new_id).exists(), "失败回滚不留孤儿目录"


async def test_pull_deadline_falls_back(rcache_env, monkeypatch):
    from nonebot_plugin_parser.api import rcache

    """拉取超过整体 deadline → 未命中走原解析（请求不被拖死），目录回滚。"""
    from nonebot_plugin_parser.api import media

    fake = rcache_env
    _make_entry(fake, host="host-b")

    async def slow_stream(*a, **kw):
        await asyncio.sleep(30)

    class FakeStreamResp:
        status_code = 200
        headers = {"content-length": "10"}

        def aiter_bytes(self):
            async def _gen():
                yield b"0"
                await asyncio.sleep(60)

            return _gen()

        async def __aenter__(self):
            await slow_stream()

        async def __aexit__(self, *exc):
            return False

    class FakeDirectClient:
        def stream(self, method, url, **kw):
            return FakeStreamResp()

    class FakeDownloader:
        direct_client = FakeDirectClient()

    import nonebot_plugin_parser.download as dl_mod

    monkeypatch.setattr(dl_mod, "DOWNLOADER", FakeDownloader())

    new_id = "f" * 22
    t0 = time.monotonic()
    got = await rcache.get(_URL_NORM, new_id)
    elapsed = time.monotonic() - t0
    assert got is None
    assert elapsed < rcache._PULL_DEADLINE + 3, f"超时应快速回落, 实际 {elapsed:.1f}s"
    assert not (media.MEDIA_ROOT / new_id).exists()


async def test_exdev_copy_fallback(rcache_env, monkeypatch):
    from nonebot_plugin_parser.api import rcache

    """硬链跨文件系统（EXDEV）→ copy 回退成功。"""
    from nonebot_plugin_parser.api import media

    src = media.task_media_dir(_TASK_ID) / _FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"0123456789")

    real_link = __import__("os").link

    def link_raises_exdev(src, dst, **kw):
        raise OSError(18, "Invalid cross-device link")

    import os

    monkeypatch.setattr(os, "link", link_raises_exdev)
    fake = rcache_env
    _make_entry(fake)
    got = await rcache.get(_URL_NORM, "g" * 22)
    assert got is not None, "EXDEV 应回退复制"
    assert (media.task_media_dir("g" * 22) / _FILENAME).read_bytes() == b"0123456789"
    _ = real_link


async def test_quota_exceeded_rolls_back(rcache_env, monkeypatch):
    from nonebot_plugin_parser.api import rcache

    """配额不足 → 整体回滚为未命中（不 500、不留半份文件）。"""
    from nonebot_plugin_parser.api import media

    src = media.task_media_dir(_TASK_ID) / _FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"0123456789")

    async def reserve_full(*a, **k):
        raise media.MediaQuotaExceeded("full")

    monkeypatch.setattr(media.quota, "reserve", reserve_full)
    fake = rcache_env
    _make_entry(fake)
    got = await rcache.get(_URL_NORM, "h" * 22)
    assert got is None
    assert not (media.MEDIA_ROOT / ("h" * 22)).exists()


async def test_entry_freshness_ttl_not_exceed_task_ttl():
    """条目生效 TTL 不得超过媒体任务保留期 - 600s。"""
    from nonebot_plugin_parser.api import rcache
    from nonebot_plugin_parser.config import pconfig

    assert rcache.eff_ttl() <= pconfig.api_task_ttl - 600


async def test_exp_span_bounds(monkeypatch):
    """自签 URL 有效期：地板 60s、封顶 sig_ttl-5。"""
    from nonebot_plugin_parser.api import rcache
    from nonebot_plugin_parser.config import pconfig

    monkeypatch.setattr(pconfig, "parser_api_media_sig_ttl", 3600)
    assert rcache._exp_span() == 3300
    monkeypatch.setattr(pconfig, "parser_api_media_sig_ttl", 120)
    assert rcache._exp_span() == 60  # 地板
    monkeypatch.setattr(pconfig, "parser_api_media_sig_ttl", 62)
    assert rcache._exp_span() == 57  # 封顶 ttl-5


async def test_local_only_mode_isolated(rcache_env, tmp_path, monkeypatch):
    from nonebot_plugin_parser.api import rcache

    """local-only（shared_secret 空）：nonce 派生验签通过；异机伪造本机条目验签失败。"""
    from nonebot_plugin_parser.api import media
    from nonebot_plugin_parser.config import pconfig

    monkeypatch.setattr(pconfig, "parser_rcache_shared_secret", "")
    rcache.reset_keys()
    nonce_file = tmp_path / "rcache_instance.json"
    monkeypatch.setattr(rcache, "_nonce_file", lambda: nonce_file)

    src = media.task_media_dir(_TASK_ID) / _FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"0123456789")

    fake = rcache_env
    _make_entry(fake)  # 用本机 nonce 派生的密钥签名
    got = await rcache.get(_URL_NORM, "i" * 22)
    assert got is not None, "local-only 本机条目应正常复用"

    # 异机伪造：用不同密钥签名（模拟另一台 local-only nonce 不同）
    rcache._sig_key_cache = hashlib.sha256(b"other-machine-key").digest()[:32]
    fake2 = fake
    key = rcache.make_key("https://example.com/watch/3")
    entry = {
        "v": 1,
        "host": "host-a",  # 伪造本机 host
        "origin_task_id": _TASK_ID,
        "platform": "test",
        "url_key": rcache.url_key("https://example.com/watch/3"),
        "cfg_fp": rcache.cfg_fingerprint(),
        "created_at": int(time.time()),
        "payload": _PAYLOAD,
        "staged_files": _STAGED,
        "staged_bytes": 10,
    }
    blob = rcache._encode_entry(entry)
    fake2.store[key] = blob
    rcache._sig_key_cache = None  # 恢复本机密钥
    got = await rcache.get("https://example.com/watch/3", "j" * 22)
    assert got is None, "伪造条目验签必须失败"


async def test_heartbeat_no_false_alarm_on_restart(rcache_env, monkeypatch):
    """重启（nonce 复用持久文件）→ 心跳不误报；异机 nonce → 告警。"""
    from nonebot_plugin_parser.api import rcache

    fake = rcache_env
    # 第一次启动：写 nonce 文件 + 心跳
    await rcache.heartbeat()
    inst_key = f"{rcache._INSTANCE_KEY_PREFIX}host-a"
    first = json.loads(fake.store[inst_key])
    # 模拟重启：模块级 nonce 缓存清空但持久文件不变
    rcache._heartbeat_nonce = None
    warns = []

    import nonebot

    monkeypatch.setattr(nonebot.logger, "warning", lambda msg, *a, **k: warns.append(msg))
    await rcache.heartbeat()
    assert not warns, "同机重启不应误报 instance_id 撞车"

    # 异机同 id：不同 nonce → 告警（生产 redis GET 返回 bytes，直接写 store 也要编码）
    fake.store[inst_key] = json.dumps({"nonce": "other-machine-nonce", "ts": int(time.time())}).encode()
    rcache._heartbeat_nonce = None
    await rcache.heartbeat()
    assert any("instance_id" in w for w in warns), "异机同 id 应告警"


async def test_media_key_matches_routes_sign_key(rcache_env, monkeypatch):
    """rcache 自签媒体 URL 与 routes 媒体签名必须同密钥源——否则对端校验 403
    （生产实测事故：rcache 用 shared_secret 派生而 routes 用 api_token，签名互相不认）。"""
    from nonebot_plugin_parser.api import rcache, routes
    from nonebot_plugin_parser.config import pconfig

    # 共享密钥模式：两侧同源
    monkeypatch.setattr(pconfig, "parser_rcache_shared_secret", "shared-s3cret")
    rcache.reset_keys()
    routes.reset_sign_key()
    assert rcache._media_key() == routes._sign_key()

    # 回落模式：双侧都落到 api_token
    monkeypatch.setattr(pconfig, "parser_rcache_shared_secret", "")
    rcache.reset_keys()
    routes.reset_sign_key()
    assert rcache._media_key() == routes._sign_key()


async def test_validate_config_warns(rcache_env, monkeypatch):
    """instance_id 空缺 / peer_map 含本机 / TTL 过短的启动告警。"""
    from nonebot_plugin_parser.api import rcache
    from nonebot_plugin_parser.config import pconfig

    monkeypatch.setattr(pconfig, "parser_rcache_enabled", True)
    monkeypatch.setattr(pconfig, "parser_rcache_redis_url", "redis://x")
    monkeypatch.setattr(pconfig, "parser_rcache_instance_id", "")
    warns = rcache.validate_config()
    assert any("instance_id" in w for w in warns)

    monkeypatch.setattr(pconfig, "parser_rcache_instance_id", "host-a")
    monkeypatch.setattr(pconfig, "parser_rcache_peer_map", {"host-a": "http://x"})
    warns = rcache.validate_config()
    assert any("peer_map" in w for w in warns)


async def test_concurrent_same_url_local_reuse(rcache_env):
    from nonebot_plugin_parser.api import rcache

    """并发同链接提交复用：两请求各自硬链到各自目录，互不破坏。"""
    from nonebot_plugin_parser.api import media

    src = media.task_media_dir(_TASK_ID) / _FILENAME
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"0123456789")
    fake = rcache_env
    _make_entry(fake)
    results = await asyncio.gather(*(rcache.get(_URL_NORM, x * 22) for x in ("k", "l")))
    assert all(r is not None for r in results)
    for tid in ("k" * 22, "l" * 22):
        assert (media.task_media_dir(tid) / _FILENAME).is_file()
