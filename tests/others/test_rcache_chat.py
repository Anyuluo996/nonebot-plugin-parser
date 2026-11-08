"""聊天链路复用结果缓存（二期）测试。

覆盖：重放重建的字节级往返保真（P1-C）、reply/at 剥离（P1-A）、媒体引用白名单
（P2-A）、命中后清场与配额归还（P1-1）、staged 回写（P1-2）、seed 门控矩阵
与异常清场（P2-C/P2-3）、_send_message_list 的 sent_flag 语义（P1-B 零发出
判定的机制层）、store 的 cache_hit/update_staged（P1-2）。

redis 用内存 fake 桩（与 test_rcache.py 同款）。nonebot_plugin_parser.* 的
import 一律放测试函数内部（conftest 的 session fixture 先完成插件加载）。
"""

import copy
import time

import pytest

_URL_NORM = "https://example.com/watch/1"
_TASK_ID = "a" * 22
_CHAT_ID = "c" * 22
_F_JPG = "b" * 22 + ".jpg"
_MEDIA_BYTES = b"PNGDATA"


class FakeRedis:
    """内存 redis 桩（与 test_rcache.py 同款：get/set/setex/delete/eval CAS）。"""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, keepttl=False):
        if isinstance(value, str):
            value = value.encode()
        if keepttl and key in self.store:
            self.store[key] = value
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


class _FakeAdapter:
    def __init__(self, name="OneBot V11"):
        self._name = name

    def get_name(self):
        return self._name


class _FakeBot:
    def __init__(self, name="OneBot V11", self_id="12345"):
        self.adapter = _FakeAdapter(name)
        self.self_id = self_id


class _FakeParser:
    """仅提供 _key_patterns 形态（seed 的 parser 级 skip 判定输入）。"""

    def __init__(self, keywords):
        self._key_patterns = [(kw, None) for kw in keywords]


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
    monkeypatch.setattr(rcache, "_nonce_file", lambda: tmp_path / "rcache_instance.json")
    return fake


@pytest.fixture
async def chat_db(tmp_path, monkeypatch):
    """隔离 SQLite：store._DB_PATH 指向 tmp 并初始化（try_replay 的 staged 回写用）。"""
    from nonebot_plugin_parser.api import store

    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "api_tasks.db")
    await store.init_db()
    return store


@pytest.fixture
async def chat_row(chat_db):
    """预建一条 running 聊天行（命中路径的行终态/回写落点）。"""
    from nonebot_plugin_parser.api import store

    await store.create_task(_CHAT_ID, _URL_NORM, _URL_NORM, status="running", source="chat", started=True)
    return _CHAT_ID


@pytest.fixture
def ob11_ctx():
    """伪造 OneBot V11 bot 上下文（适配器门控 + node uid 本机化取值）。"""
    from nonebot.matcher import current_bot

    bot = _FakeBot()
    token = current_bot.set(bot)
    yield bot
    current_bot.reset(token)


# ── 段结构工具 ───────────────────────────────────────────────────────


def _normalize_export(exported):
    """导出段 → dict 形态（node content 的 Message 展开为段数组），与 serialize 同构。"""
    out = []
    for seg in exported:
        d = dict(seg.data)
        c = d.get("content")
        if isinstance(c, list):
            d["content"] = [{"type": t.type, "data": dict(t.data)} for t in c]
        out.append({"type": seg.type, "data": d})
    return out


def _canon_paths(segments):
    """file:// 的本地路径不参与比对：源路径（首次发送）与复用目录路径必然不同，
    语义等价（同一媒体字节），仅比对段结构与引用形态。递归含 node content 嵌套。"""
    out = copy.deepcopy(segments)

    def _walk(items):
        for seg in items:
            data = seg.get("data")
            if not isinstance(data, dict):
                continue
            v = data.get("file")
            if isinstance(v, str) and v.startswith("file://"):
                data["file"] = "file://<local>"
            content = data.get("content")
            if isinstance(content, list):
                _walk(content)

    _walk(out)
    return out


def _stage_placeholder(segments, filename):
    """模拟 serialize._convert_file_ref：file:// 与 base64:// 都转存为
    media:// 占位符（生产写路径两种形态都会 stage）。"""
    for seg in segments:
        data = seg.get("data")
        if not isinstance(data, dict):
            continue
        f = data.get("file")
        if isinstance(f, str) and f.startswith(("file://", "base64://")):
            data["file"] = f"media://{filename}"
        content = data.get("content")
        if isinstance(content, list):
            for inner in content:
                fi = inner.get("data", {}).get("file")
                if isinstance(fi, str) and fi.startswith(("file://", "base64://")):
                    inner["data"]["file"] = f"media://{filename}"


def _make_entry(fake, *, staged, payload, **overrides):
    """直接构造签名条目写入 fake redis（绕过 put，控制字段）。"""
    from nonebot_plugin_parser.api import rcache

    key = rcache.make_key(_URL_NORM)
    entry = {
        "v": 1,
        "host": "host-a",
        "origin_task_id": _TASK_ID,
        "platform": "test",
        "url_key": rcache.url_key(_URL_NORM),
        "cfg_fp": rcache.cfg_fingerprint(),
        "created_at": int(time.time()),
        "payload": payload,
        "staged_files": staged,
        "staged_bytes": sum(f["size"] for f in staged),
    }
    entry.update(overrides)
    fake.store[key] = rcache._encode_entry(entry)
    fake.ttl[key] = rcache.eff_ttl()
    return key


def _seed_src_file():
    """在源任务目录写入媒体文件（rcache.get 本机硬链复用的前提）。"""
    from nonebot_plugin_parser.api import media

    src = media.task_media_dir(_TASK_ID) / _F_JPG
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_MEDIA_BYTES)
    return src


# ── 字节级往返保真（P1-C） ───────────────────────────────────────────


async def test_replay_roundtrip_path_form(rcache_env, chat_row, ob11_ctx, monkeypatch):
    """file:// 形态：重建 + 再导出与原始段结构逐字段相等（本地路径差异规范化）。"""
    from nonebot_plugin_alconna.uniseg import Text, Image, UniMessage, SupportAdapter

    from nonebot_plugin_parser.api import media, store
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers import rcache_link

    monkeypatch.setattr(pconfig, "parser_use_base64", False)
    src = _seed_src_file()
    first_send = _normalize_export(
        await UniMessage([Text("你好"), Image(path=src)]).export(adapter=SupportAdapter.onebot11)
    )
    payload_msgs = copy.deepcopy(first_send)
    _stage_placeholder(payload_msgs, _F_JPG)
    fake = rcache_env
    staged = [{"filename": _F_JPG, "size": len(_MEDIA_BYTES)}]
    _make_entry(fake, staged=staged, payload={"metadata": {}, "messages": [payload_msgs]})

    msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    assert msgs is not None
    assert len(msgs) == 1
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert _canon_paths(re_exp) == _canon_paths(first_send)
    # P1-2：staged 回写进聊天行（端点反查依赖）
    assert await store.get_staged_files(_CHAT_ID) == [{"filename": _F_JPG, "size": len(_MEDIA_BYTES)}]
    assert media.task_media_dir(_CHAT_ID).is_dir(), "命中后媒体目录保留（供 file:// 发送）"


async def test_replay_roundtrip_base64_form(rcache_env, chat_row, ob11_ctx, monkeypatch):
    """base64:// 形态（use_base64=True）：重放按本机配置读盘回 bytes，
    字节级完全相等（含媒体内容本身，不只结构）。"""
    from nonebot_plugin_alconna.uniseg import Text, Image, UniMessage, SupportAdapter

    from nonebot_plugin_parser.api import store
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers import rcache_link

    monkeypatch.setattr(pconfig, "parser_use_base64", True)
    _seed_src_file()
    first_send = _normalize_export(
        await UniMessage([Text("hi"), Image(raw=_MEDIA_BYTES)]).export(adapter=SupportAdapter.onebot11)
    )
    payload_msgs = copy.deepcopy(first_send)
    _stage_placeholder(payload_msgs, _F_JPG)
    fake = rcache_env
    staged = [{"filename": _F_JPG, "size": len(_MEDIA_BYTES)}]
    _make_entry(fake, staged=staged, payload={"metadata": {}, "messages": [payload_msgs]})

    msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    assert msgs is not None
    assert len(msgs) == 1
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert re_exp == first_send, "base64 形态应字节级相等（含媒体内容）"
    assert await store.get_staged_files(_CHAT_ID) is not None


async def test_replay_roundtrip_forward_nodes(rcache_env, chat_row, ob11_ctx, monkeypatch):
    """合并转发 node 段：拍平的 node 重建回 Reference 再导出与原段相等
    （uid 用本机 self_id —— 与首次构造同值时全字段相等，P1-3）。"""
    from nonebot_plugin_alconna.uniseg import Text, Image, UniMessage, SupportAdapter

    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.helper import UniHelper
    from nonebot_plugin_parser.matchers import rcache_link

    monkeypatch.setattr(pconfig, "parser_use_base64", False)
    src = _seed_src_file()
    ref = UniHelper.construct_forward_message([Text("n1"), Image(path=src)], user_id="12345")
    first_send = _normalize_export(await UniMessage([ref]).export(adapter=SupportAdapter.onebot11))
    payload_msgs = copy.deepcopy(first_send)
    _stage_placeholder(payload_msgs, _F_JPG)
    fake = rcache_env
    staged = [{"filename": _F_JPG, "size": len(_MEDIA_BYTES)}]
    _make_entry(fake, staged=staged, payload={"metadata": {}, "messages": [payload_msgs]})

    msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    assert msgs is not None
    assert len(msgs) == 1
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert _canon_paths(re_exp) == _canon_paths(first_send)
    # node 的 uid/nickname 与首次导出一致（本条目源 uid 即本机 self_id，全字段相等）
    assert re_exp[0]["data"]["user_id"] == first_send[0]["data"]["user_id"]
    assert first_send[0]["data"]["user_id"] == "12345"


async def test_replay_node_uid_localized(rcache_env, chat_row, monkeypatch):
    """P1-3：源条目 node uid ≠ 本机 self_id 时，重建后导出的 user_id 为本机值。"""
    from nonebot_plugin_alconna.uniseg import Text, UniMessage, SupportAdapter

    from nonebot_plugin_parser.api import store
    from nonebot_plugin_parser.helper import UniHelper
    from nonebot_plugin_parser.matchers import rcache_link

    _seed_src_file()
    ref = UniHelper.construct_forward_message([Text("n1")], user_id="99999")
    original = _normalize_export(await UniMessage([ref]).export(adapter=SupportAdapter.onebot11))
    fake = rcache_env
    _make_entry(fake, staged=[], payload={"metadata": {}, "messages": [original]})

    ob11_ctx = _FakeBot(self_id="12345")
    from nonebot.matcher import current_bot

    token = current_bot.set(ob11_ctx)
    try:
        msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    finally:
        current_bot.reset(token)
    assert msgs is not None
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert re_exp[0]["data"]["user_id"] == "12345"
    assert re_exp[0]["data"]["user_id"] != original[0]["data"]["user_id"]
    assert await store.get_staged_files(_CHAT_ID) == []


async def test_replay_strips_reply_at(rcache_env, chat_row, ob11_ctx):
    """P1-A：payload 内的 reply/at 段在重放后不出现。"""
    from nonebot_plugin_alconna.uniseg import SupportAdapter

    from nonebot_plugin_parser.api import store
    from nonebot_plugin_parser.matchers import rcache_link

    _seed_src_file()
    original = [
        {"type": "reply", "data": {"id": "999"}},
        {"type": "text", "data": {"text": "t"}},
        {"type": "at", "data": {"qq": "10086"}},
    ]
    fake = rcache_env
    _make_entry(fake, staged=[], payload={"metadata": {}, "messages": [original]})
    msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    assert msgs is not None
    assert len(msgs) == 1
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert [s["type"] for s in re_exp] == ["text"]
    assert await store.get_staged_files(_CHAT_ID) == []


async def test_replay_face_and_unknown_segments(rcache_env, chat_row, ob11_ctx):
    """face 直构与未知段 of() 兜底：导出与原始段相等（music 段走 Other 透传）。"""
    from nonebot_plugin_alconna.uniseg import SupportAdapter

    from nonebot_plugin_parser.matchers import rcache_link

    original = [
        {"type": "face", "data": {"id": "4"}},
        {"type": "music", "data": {"type": "custom", "url": "https://example.com/s", "title": "t"}},
    ]
    fake = rcache_env
    _make_entry(fake, staged=[], payload={"metadata": {}, "messages": [original]})
    msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    assert msgs is not None
    assert len(msgs) == 1
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert re_exp == original


async def test_replay_http_media_passthrough(rcache_env, chat_row, ob11_ctx):
    """远程公开 URL 的 file 引用原样回放（与首次导出形态一致）。"""
    from nonebot_plugin_alconna.uniseg import Image, UniMessage, SupportAdapter

    from nonebot_plugin_parser.matchers import rcache_link

    first_send = _normalize_export(
        await UniMessage([Image(url="https://cdn.example.com/x.jpg")]).export(adapter=SupportAdapter.onebot11)
    )
    fake = rcache_env
    _make_entry(fake, staged=[], payload={"metadata": {}, "messages": [first_send]})
    msgs = await rcache_link.try_replay(_URL_NORM, _CHAT_ID)
    assert msgs is not None
    re_exp = _normalize_export(await msgs[0].export(adapter=SupportAdapter.onebot11))
    assert re_exp == first_send


# ── 门控（不触 Redis / 回源） ────────────────────────────────────────


async def test_replay_gates(rcache_env, monkeypatch):
    """chat_task_id None / rcache 关闭 / 非 OneBot 适配器 / 无 bot 上下文 → None
    且不触碰 Redis（条目原样保留）。"""
    from nonebot.matcher import current_bot

    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers import rcache_link

    fake = rcache_env
    plain = [[{"type": "text", "data": {"text": "x"}}]]
    key = _make_entry(fake, staged=[], payload={"metadata": {}, "messages": plain})

    assert await rcache_link.try_replay(_URL_NORM, None) is None
    monkeypatch.setattr(pconfig, "parser_rcache_enabled", False)
    assert await rcache_link.try_replay(_URL_NORM, _CHAT_ID) is None
    monkeypatch.setattr(pconfig, "parser_rcache_enabled", True)

    token = current_bot.set(_FakeBot(name="Telegram"))
    try:
        assert await rcache_link.try_replay(_URL_NORM, _CHAT_ID) is None
    finally:
        current_bot.reset(token)
    assert key in fake.store, "门控失败不得触碰 Redis"


# ── 清场与配额（P1-1 / 小问题·清场范围） ─────────────────────────────


async def test_replay_rebuild_failure_cleans_dir_and_quota(rcache_env, chat_row, ob11_ctx, monkeypatch):
    """get 命中（媒体已物化+配额已扣）后重建抛异常 → 目录清场 + 配额归还 → None。"""
    from nonebot_plugin_parser.api import media
    from nonebot_plugin_parser.matchers import rcache_link

    _seed_src_file()
    original = [{"type": "text", "data": {"text": "x"}}]
    _make_entry(rcache_env, staged=[], payload={"metadata": {}, "messages": [original]})

    async def _boom(_msgs):
        raise RuntimeError("rebuild exploded")

    monkeypatch.setattr(rcache_link, "_rebuild_messages", _boom)
    used_before = media.quota.used
    assert await rcache_link.try_replay(_URL_NORM, _CHAT_ID) is None
    assert not media.task_media_dir(_CHAT_ID).exists(), "清场后目录不得残留"
    assert media.quota.used == used_before, "配额必须归还"


async def test_replay_update_staged_failure_cleans_dir(rcache_env, chat_row, ob11_ctx, monkeypatch):
    """update_staged 的 DB 异常同样走清场（小问题·清场范围）。"""
    from nonebot_plugin_parser.api import media, store
    from nonebot_plugin_parser.matchers import rcache_link

    _seed_src_file()
    staged = [{"filename": _F_JPG, "size": len(_MEDIA_BYTES)}]
    _make_entry(
        rcache_env, staged=staged, payload={"metadata": {}, "messages": [[{"type": "text", "data": {"text": "x"}}]]}
    )

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "update_staged", _boom)
    used_before = media.quota.used
    assert await rcache_link.try_replay(_URL_NORM, _CHAT_ID) is None
    assert not media.task_media_dir(_CHAT_ID).exists()
    assert media.quota.used == used_before


async def test_replay_placeholder_missing_falls_back(rcache_env, chat_row, ob11_ctx):
    """staged 文件在本机缺失（源目录被清但键未死的竞态兜底）→ 未命中 → None。"""
    from nonebot_plugin_parser.api import media
    from nonebot_plugin_parser.matchers import rcache_link

    # 刻意不写源文件：_reuse_local 会因源缺失直接删键（rcache 层自愈路径）
    fake = rcache_env
    payload = {"metadata": {}, "messages": [[{"type": "image", "data": {"file": f"media://{_F_JPG}"}}]]}
    key = _make_entry(fake, staged=[{"filename": _F_JPG, "size": 8}], payload=payload)
    assert await rcache_link.try_replay(_URL_NORM, _CHAT_ID) is None
    assert key not in fake.store
    assert not media.task_media_dir(_CHAT_ID).exists()


# ── rcache 白名单不变量（P2-A） ──────────────────────────────────────


def _entry_dict(staged, payload):
    from nonebot_plugin_parser.api import rcache

    return {
        "v": 1,
        "host": "host-a",
        "origin_task_id": _TASK_ID,
        "platform": "",
        "url_key": rcache.url_key(_URL_NORM),
        "cfg_fp": rcache.cfg_fingerprint(),
        "created_at": int(time.time()),
        "payload": payload,
        "staged_files": staged,
        "staged_bytes": sum(f["size"] for f in staged),
    }


async def test_validate_entry_whitelist_matrix():
    """file 键白名单 / url、path 键本地形态拒绝 / text 键字面不误杀。"""
    from nonebot_plugin_parser.api import rcache

    staged = [{"filename": _F_JPG, "size": 3}]
    uk = rcache.url_key(_URL_NORM)

    def validate(payload):
        return rcache._validate_entry(_entry_dict(staged, payload), uk)

    assert validate({"metadata": {}, "messages": [[{"type": "image", "data": {"file": f"media://{_F_JPG}"}}]]})
    assert validate(
        {"metadata": {}, "messages": [[{"type": "image", "data": {"file": "https://cdn.example.com/x.jpg"}}]]}
    )
    assert validate(
        {"metadata": {}, "messages": [[{"type": "text", "data": {"text": f"media://{'z' * 22}.jpg 和 ../../etc"}}]]}
    )

    for bad in ("file:///etc/passwd", "/abs/path.jpg", f"media://{'z' * 22}.jpg", "base64://AAAA"):
        assert not validate({"metadata": {}, "messages": [[{"type": "image", "data": {"file": bad}}]]}), bad

    assert not validate({"metadata": {"url": "file:///x"}, "messages": []})
    assert not validate({"metadata": {"url": "/abs"}, "messages": []})
    assert validate({"metadata": {"url": "https://example.com/page"}, "messages": []})
    assert not validate({"metadata": {}, "messages": [[{"type": "text", "data": {"text": "t", "path": "/etc/x"}}]]})

    # node content 嵌套内的违规同样被拦截
    nested_bad = [{"type": "image", "data": {"file": "file:///etc/p"}}]
    node_payload = {
        "metadata": {},
        "messages": [[{"type": "node", "data": {"user_id": 1, "nickname": "n", "content": nested_bad}}]],
    }
    assert not validate(node_payload)


# ── seed 门控与异常清场（P2-C / P2-3 / P1-2） ────────────────────────


async def test_seed_success_writes_put_and_staged(rcache_env, chat_db, monkeypatch):
    """种缓存主路径：build_result → update_staged → put 参数正确。"""
    from nonebot_plugin_parser.api import media, store, rcache, serialize
    from nonebot_plugin_parser.matchers import rcache_link

    calls: dict = {}

    async def fake_build(result, task_id):
        calls["build"] = task_id
        return ({"metadata": {}, "messages": []}, [{"filename": _F_JPG, "size": 3}], 3, False)

    async def fake_put(url_norm, payload, staged, staged_bytes, origin, keyword, *, degraded=False):
        calls["put"] = (url_norm, origin, keyword, degraded)

    monkeypatch.setattr(serialize, "build_result", fake_build)
    monkeypatch.setattr(rcache, "put", fake_put)

    await store.create_task(_CHAT_ID, _URL_NORM, _URL_NORM, status="running", source="chat", started=True)
    media.task_media_dir(_CHAT_ID).mkdir(parents=True, exist_ok=True)
    await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["bili"]), _CHAT_ID)
    assert calls["build"] == _CHAT_ID
    assert calls["put"] == (_URL_NORM, _CHAT_ID, "bili", False)
    assert await store.get_staged_files(_CHAT_ID) == [{"filename": _F_JPG, "size": 3}]


async def test_seed_gates(rcache_env, chat_db, monkeypatch):
    """门控矩阵：chat_task_id None / rcache 关闭 / parser 级 skip / 信号量占满 →
    均不触 build_result。"""
    from nonebot_plugin_parser.api import serialize
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers import rcache_link

    calls: list = []

    async def fake_build(result, task_id):
        calls.append(task_id)
        return ({}, [], 0, False)

    monkeypatch.setattr(serialize, "build_result", fake_build)

    await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["bili"]), None)
    assert not calls, "chat_task_id=None 必须直接跳过（P2-D 的进程内缓存命中分支即此形态）"

    monkeypatch.setattr(pconfig, "parser_rcache_enabled", False)
    await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["bili"]), _CHAT_ID)
    assert not calls
    monkeypatch.setattr(pconfig, "parser_rcache_enabled", True)

    monkeypatch.setattr(pconfig, "parser_rcache_skip_keywords", ["live.bili"])
    await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["live.bili", "bili"]), _CHAT_ID)
    await rcache_link.seed_from_result(object(), _URL_NORM, "b23", _FakeParser(["b23", "live.bili"]), _CHAT_ID)
    assert not calls, "parser 任一 keyword 命中 skip 即跳过"
    monkeypatch.setattr(pconfig, "parser_rcache_skip_keywords", [])

    await rcache_link._SEED_SEM.acquire()
    await rcache_link._SEED_SEM.acquire()
    try:
        await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["bili"]), _CHAT_ID)
        assert not calls, "信号量占满时忙跳过（P2-C）"
    finally:
        rcache_link._SEED_SEM.release()
        rcache_link._SEED_SEM.release()


async def test_seed_build_failure_cleans_dir(rcache_env, chat_db, monkeypatch):
    """build_result 抛异常 → 半拉目录清场。"""
    from nonebot_plugin_parser.api import media, serialize
    from nonebot_plugin_parser.matchers import rcache_link

    async def fake_build(result, task_id):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(serialize, "build_result", fake_build)
    # 配额基线取 stage 之前：断言"清场把 staging 占用完整归还"
    base_used = media.quota.used
    src = media.MEDIA_ROOT / "seed-src.bin"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    dir_ = media.task_media_dir(_CHAT_ID)
    await media.stage_path(src, dir_)
    await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["bili"]), _CHAT_ID)
    assert not dir_.exists()
    assert media.quota.used == base_used, "清场必须把 staging 占用完整归还"


async def test_seed_put_failure_keeps_dir(rcache_env, chat_db, monkeypatch):
    """put 抛异常 → 目录保留（行已带 staged 清单，TTL 实测释放）。"""
    from nonebot_plugin_parser.api import media, store, rcache, serialize
    from nonebot_plugin_parser.matchers import rcache_link

    async def fake_build(result, task_id):
        return ({}, [{"filename": _F_JPG, "size": 1}], 1, False)

    async def fake_put(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(serialize, "build_result", fake_build)
    monkeypatch.setattr(rcache, "put", fake_put)
    await store.create_task(_CHAT_ID, _URL_NORM, _URL_NORM, status="running", source="chat", started=True)
    dir_ = media.task_media_dir(_CHAT_ID)
    dir_.mkdir(parents=True)
    await rcache_link.seed_from_result(object(), _URL_NORM, "bili", _FakeParser(["bili"]), _CHAT_ID)
    assert dir_.exists(), "put 失败保留目录"


# ── _send_message_list sent_flag（P1-B 机制层） ──────────────────────


async def test_send_flag_success_and_partial(monkeypatch):
    """成功置位；部分发出后异常上抛且 flag 已置位；二次重试成功也置位。"""
    from nonebot_plugin_alconna.uniseg import Text, UniMessage

    from nonebot_plugin_parser.matchers import _send_message_list

    async def fake_send(self, *a, **k):
        return None

    monkeypatch.setattr(UniMessage, "send", fake_send)
    flag: list[bool] = []
    await _send_message_list([UniMessage([Text("a")]), UniMessage([Text("b")])], sent_flag=flag)
    assert flag == [True, True]

    calls = {"n": 0}

    async def flaky_send(self, *a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("protocol down")

    monkeypatch.setattr(UniMessage, "send", flaky_send)
    flag2: list[bool] = []
    try:
        await _send_message_list([UniMessage([Text("ok")]), UniMessage([Text("bad")])], sent_flag=flag2)
    except RuntimeError:
        pass
    assert flag2 == [True], "第一条已发出，零发出判定不成立"


async def test_send_flag_forward_degrade(monkeypatch):
    """合并转发降级：父消息失败 → 逐条直发；全部节点失败 → flag 为空且不抛（零发出）。"""
    from nonebot_plugin_alconna.uniseg import Text, Reference, UniMessage

    from nonebot_plugin_parser.helper import UniHelper
    from nonebot_plugin_parser.matchers import _send_message_list

    fwd = UniMessage([UniHelper.construct_forward_message([Text("n1"), Text("n2")], user_id="1")])

    async def all_fail(self, *a, **k):
        raise RuntimeError("napcat down")

    monkeypatch.setattr(UniMessage, "send", all_fail)
    flag: list[bool] = []
    await _send_message_list([fwd], sent_flag=flag)
    assert flag == [], "全部降级节点失败 = 零发出"

    mixed: list = []

    async def parent_fail_nodes_ok(self, *a, **k):
        if any(isinstance(s, Reference) for s in self):
            raise RuntimeError("fwd down")
        mixed.append(True)

    monkeypatch.setattr(UniMessage, "send", parent_fail_nodes_ok)
    flag2: list[bool] = []
    await _send_message_list([fwd], sent_flag=flag2)
    assert flag2 == [True] * 2, "降级逐条成功的节点逐个置位"


# ── store：cache_hit 与 update_staged（P1-2） ────────────────────────


async def test_store_set_succeeded_cache_hit(chat_db):
    from nonebot_plugin_parser.api import store

    tid = "s" * 22
    await store.create_task(tid, "u", "u", status="running", started=True)
    assert await store.set_succeeded(tid, cache_hit=True)
    row = await store.get_task(tid)
    assert row["status"] == "succeeded"
    assert row["cache_hit"] == 1

    tid2 = "t" * 22
    await store.create_task(tid2, "u", "u", status="running", started=True)
    await store.set_succeeded(tid2)
    assert (await store.get_task(tid2))["cache_hit"] == 0


async def test_store_update_staged_roundtrip(chat_db):
    from nonebot_plugin_parser.api import store

    tid = "u" * 22
    await store.create_task(tid, "u", "u", status="running", started=True)
    staged = [{"filename": _F_JPG, "size": 8}]
    assert await store.update_staged(tid, staged, 8)
    assert await store.get_staged_files(tid) == staged
    assert (await store.get_task(tid))["staged_bytes"] == 8


async def test_record_chat_result_cache_hit(chat_db, monkeypatch):
    """chat.record_chat_result 透传 cache_hit（默认 False）。"""
    from nonebot_plugin_parser.api import chat, store

    monkeypatch.setattr(chat, "_recording_enabled", True)
    tid = "v" * 22
    await store.create_task(tid, "u", "u", status="running", source="chat", started=True)
    await chat.record_chat_result(tid, ok=True, cache_hit=True)
    assert (await store.get_task(tid))["cache_hit"] == 1
