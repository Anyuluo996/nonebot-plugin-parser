"""匹配层 / 音乐 / 失败重试链路的回归测试（离线，不碰网络与真实消息管线）。

覆盖的已确认缺陷：
1. ``auth.resolve_item`` 只查「去前缀」命令名，导致 ``par授权 @某人 parqq登录``
   在 parser_force_prefix 非空时报「未识别的受控项」。
2. ``tg授权``/``tg取消授权`` 用 ``extract_plain_text()``，OneBot 的 at 段被丢掉；
   手打 ``@alice`` 被原样写入永不命中的白名单。
3. 2FA pending 不校验过期、不校验内容形态：窗口内该用户任意消息被当密码吞掉。
4. 点歌搜索借 ``get_parser_by_type(NCMParser)``，网易云被关闭时 ValueError
   让整组点歌命令静默无回复。
5. L2 无差别 ``except Exception``，把 Tip/Ignore 语义异常重试到上限后误报 L3。
"""

import time
import types

import pytest

# ── 1. auth.resolve_item 前缀形态 ──────────────────────────────────


@pytest.fixture
def par_prefix(monkeypatch):
    """把 parser_force_prefix 固定成 ``par``（真实部署里的常见配置）。"""
    from nonebot_plugin_parser.config import Config

    monkeypatch.setattr(Config, "parse_prefix", property(lambda self: "par"))


def test_resolve_item_accepts_prefixed_command(par_prefix):
    """带前缀的真实命令名（credential.py 就是这样登记的）必须能解析。"""
    from nonebot_plugin_parser.matchers import auth

    # 模拟 credential._register_music_login_commands 的登记形态
    auth.register_command_item("parqq登录", auth.QQ_LOGIN)

    assert auth.resolve_item("parqq登录") == auth.QQ_LOGIN
    # 去前缀形态（用户手打 qq登录，恰好也是语义键）
    assert auth.resolve_item("qq登录") == auth.QQ_LOGIN


def test_resolve_item_falls_back_to_stripped_command(par_prefix):
    """登记的键不带前缀时，用户带前缀输入仍要命中（反向兼容）。"""
    from nonebot_plugin_parser.matchers import auth

    assert "dycookie" in auth.COMMAND_TO_ITEM  # 硬编码登记的裸命令名
    assert auth.resolve_item("pardycookie") == auth.DY_COOKIE


def test_resolve_item_unknown_returns_none(par_prefix):
    from nonebot_plugin_parser.matchers import auth

    assert auth.resolve_item("完全不存在的受控项") is None
    assert auth.resolve_item("  ") is None


# ── 2. tg授权 用户标识解析（at 段优先 + 纯文本必须纯数字）─────────


def _onebot_message(*segments):
    from nonebot.adapters.onebot.v11 import Message

    msg = Message()
    for seg in segments:
        msg = msg + seg
    return msg


def test_extract_tg_user_prefers_at_segment():
    """OneBot 的 at 段不进 extract_plain_text()，必须从 at 段取数字 id。"""
    from nonebot.adapters.onebot.v11 import MessageSegment

    from nonebot_plugin_parser.matchers.tg_login import _extract_tg_user

    msg = _onebot_message(MessageSegment.at(123456), " 123456")
    user_id, raw = _extract_tg_user(msg)
    assert user_id == "123456"
    assert raw == "123456"


def test_extract_tg_user_text_fallback_must_be_digits():
    """回退纯文本：@alice 这类非数字标识返回 None（旧实现写进白名单却永不命中）。"""
    from nonebot_plugin_parser.matchers.tg_login import _extract_tg_user

    assert _extract_tg_user(_onebot_message("123456")) == ("123456", "123456")
    assert _extract_tg_user(_onebot_message("@alice")) == (None, "alice")
    assert _extract_tg_user(_onebot_message("")) == (None, "")


def test_extract_tg_user_rejects_at_all():
    """@全体成员 的 qq="all" 不是用户 id。"""
    from nonebot.adapters.onebot.v11 import MessageSegment

    from nonebot_plugin_parser.matchers.tg_login import _extract_tg_user

    assert _extract_tg_user(_onebot_message(MessageSegment.at("all"))) == (None, "all")


def test_normalize_user_id_keeps_admin_contract():
    """admin._normalize_user_id 仍是共享 helper（tg_login 不再依赖它的宽松语义）。"""
    from nonebot_plugin_parser.matchers import _normalize_user_id

    assert _normalize_user_id("12345") == "12345"
    assert _normalize_user_id("@alice") == "alice"
    assert _normalize_user_id("   ") is None


# ── 3. 2FA pending：过期不消费 + 消费后清理 + 形态判断 ────────────


class _Finished(Exception):
    """替身 matcher.finish 抛出的终止信号（真实 nonebot 是 FinishedException）。"""


class _FakeMatcher:
    def __init__(self):
        self.stopped = False
        self.sent: list[str] = []
        self.finished: list[str] = []

    def stop_propagation(self) -> None:
        self.stopped = True

    async def send(self, msg) -> None:
        self.sent.append(str(msg))

    async def finish(self, msg=None) -> None:
        self.finished.append(str(msg))
        raise _Finished()


@pytest.fixture
def tg_2fa(monkeypatch):
    """重置 pending 表，并替换掉会启动 tdl 进程的下游调用。"""
    from nonebot_plugin_parser import download
    from nonebot_plugin_parser.matchers import tg_login

    tg_login._tg_2fa_pending.clear()
    submitted: list[tuple[object, str]] = []
    terminated: list[object] = []

    async def _fake_submit(handle, password):
        submitted.append((handle, password))

    async def _fake_wait(handle, timeout=30.0):
        return True

    monkeypatch.setattr(download, "submit_2fa_password", _fake_submit)
    monkeypatch.setattr(download, "wait_login_complete", _fake_wait)
    monkeypatch.setattr(tg_login, "_terminate_handle_safe", terminated.append)

    yield tg_login, submitted, terminated
    tg_login._tg_2fa_pending.clear()


def _set_event(user_id: str, text: str):
    """把 current_event 设成一个最小替身，返回 (token, event)。"""
    from nonebot.matcher import current_event

    event = types.SimpleNamespace(user_id=user_id, get_plaintext=lambda: text)
    return current_event.set(event)


def _reset_event(token) -> None:
    from nonebot.matcher import current_event

    current_event.reset(token)


@pytest.mark.asyncio
async def test_2fa_expired_does_not_consume_message(tg_2fa):
    """过期窗口内的消息不消费、不阻断，并清理 pending（终止 handle）。"""
    tg_login, submitted, terminated = tg_2fa
    handle = object()
    now = time.monotonic()
    tg_login._tg_2fa_pending["7"] = tg_login._Pending2FA(handle=handle, created_at=now - 500, expires_at=now - 1)

    matcher = _FakeMatcher()
    token = _set_event("7", "hunter2")
    try:
        await tg_login._tg_password(matcher)
    finally:
        _reset_event(token)

    assert "7" not in tg_login._tg_2fa_pending  # 已清理
    assert terminated == [handle]  # 残留 handle 被终止，防 pty fd 泄漏
    assert matcher.stopped is False  # 消息回归正常解析
    assert submitted == []  # 没有把这条消息当密码提交


@pytest.mark.asyncio
async def test_2fa_valid_password_consumes_and_cleans(tg_2fa):
    """窗口内、形态像密码的消息被消费：提交 + 阻断 + pending 删除。"""
    tg_login, submitted, terminated = tg_2fa
    handle = object()
    tg_login._tg_2fa_pending["7"] = tg_login._Pending2FA.create(handle)

    matcher = _FakeMatcher()
    token = _set_event("7", "hunter2")
    try:
        with pytest.raises(_Finished):
            await tg_login._tg_password(matcher)
    finally:
        _reset_event(token)

    assert "7" not in tg_login._tg_2fa_pending
    assert submitted == [(handle, "hunter2")]
    assert matcher.stopped is True
    assert "Telegram 登录成功" in matcher.finished[-1]
    assert terminated == []  # 正常消费路径不终止（handle 已用完）


@pytest.mark.asyncio
async def test_2fa_chat_message_is_not_swallowed(tg_2fa):
    """窗口内普通聊天（含空白/中文/指令前缀）不被当密码吞掉。"""
    tg_login, submitted, _terminated = tg_2fa
    handle = object()

    for text in ("你好呀", "par点歌 周杰伦", "https://example.com/a", ""):
        tg_login._tg_2fa_pending["7"] = tg_login._Pending2FA.create(handle)
        matcher = _FakeMatcher()
        token = _set_event("7", text)
        try:
            await tg_login._tg_password(matcher)
        finally:
            _reset_event(token)
        assert matcher.stopped is False, f"不应阻断消息: {text!r}"
        assert "7" in tg_login._tg_2fa_pending, f"不应消费 pending: {text!r}"

    assert submitted == []


def test_2fa_password_shape():
    from nonebot_plugin_parser.matchers.tg_login import _looks_like_2fa_password

    assert _looks_like_2fa_password("hunter2")
    assert _looks_like_2fa_password("a1b2c3!@#")
    assert not _looks_like_2fa_password("pass word")  # 含空白
    assert not _looks_like_2fa_password("par123")  # 命令形态
    assert not _looks_like_2fa_password("https://t.me/x")
    assert not _looks_like_2fa_password("x" * 65)  # 过长


def test_cleanup_stale_2fa_threshold_aligned_with_ttl(monkeypatch):
    """兜底清理阈值是 TTL 的若干倍（600s），不会误杀仍在 TTL 内的等待。"""
    import nonebot_plugin_parser.download.tdl as tdl_mod
    from nonebot_plugin_parser.matchers import tg_login

    terminated: list[object] = []
    monkeypatch.setattr(tdl_mod, "_terminate_handle", terminated.append)
    monkeypatch.setattr(tg_login, "_tg_2fa_pending", {})

    now = time.monotonic()
    fresh = object()
    stale = object()
    # 用了 200s（超过 TTL 但远小于兜底阈值）：正常路径该由 _tg_password 判过期，
    # 兜底清理不能抢着杀，否则过期瞬间的 `tg登录` 重试会被误终止
    tg_login._tg_2fa_pending["fresh"] = tg_login._Pending2FA(handle=fresh, created_at=now - 200, expires_at=now - 80)
    tg_login._tg_2fa_pending["stale"] = tg_login._Pending2FA(handle=stale, created_at=now - 700, expires_at=now - 580)

    removed = tg_login._cleanup_stale_2fa()
    assert removed == 1
    assert terminated == [stale]
    assert set(tg_login._tg_2fa_pending) == {"fresh"}


@pytest.mark.asyncio
async def test_2fa_pending_reexport_is_still_the_same_dict():
    """matchers 顶层 re-export 必须仍指向同一对象（__init__ 不归本改动维护）。"""
    import nonebot_plugin_parser.matchers as m
    from nonebot_plugin_parser.matchers import tg_login

    assert m._tg_2fa_pending is tg_login._tg_2fa_pending


# ── 4. 点歌 parser 查找 ────────────────────────────────────────────


def test_get_music_parser_maps_value_error_to_tip(monkeypatch):
    """网易云被 parser_disabled_platforms 关掉时只能抛 TipException。"""
    import nonebot_plugin_parser.matchers as m
    from nonebot_plugin_parser.matchers import music
    from nonebot_plugin_parser.exception import TipException

    monkeypatch.setattr(m, "KEYWORD_PARSER_MAP", {})
    with pytest.raises(TipException):
        music._get_music_parser("netease")


def test_get_http_parser_falls_back_to_any_registered(monkeypatch):
    """NCMParser 缺席时回退到任一已注册实例；全无实例返回 None。"""
    import nonebot_plugin_parser.matchers as m
    from nonebot_plugin_parser.matchers import music

    sentinel = object()
    monkeypatch.setattr(m, "KEYWORD_PARSER_MAP", {"other": sentinel})
    assert music._get_http_parser() is sentinel

    monkeypatch.setattr(m, "KEYWORD_PARSER_MAP", {})
    assert music._get_http_parser() is None


# ── 5. L2 语义异常不重试 ──────────────────────────────────────────


class _StubParser:
    def __init__(self, exc: Exception):
        self._exc = exc

    def search_url(self, url):
        return "kw", None

    async def parse(self, keyword, searched):
        raise self._exc


def _patch_retry(monkeypatch, exc):
    from nonebot_plugin_parser import failure_retry as fr

    calls: list[tuple[str, str]] = []

    async def _fake_report(record):
        calls.append(("report", record.get("url", "")))
        return True

    monkeypatch.setattr(fr, "mark_reported", lambda url: calls.append(("reported", url)))
    monkeypatch.setattr(fr, "mark_retried", lambda url, err: calls.append(("retried", err)))
    monkeypatch.setattr(fr, "mark_success", lambda url: calls.append(("success", url)))
    monkeypatch.setattr(fr, "report_failure_record", _fake_report)
    monkeypatch.setattr(fr, "_find_parser_for_url", lambda url: _StubParser(exc))
    return fr, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_name", ["TipException", "IgnoreException"])
async def test_l2_semantic_exception_marks_reported_without_retry(monkeypatch, exc_name):
    """下架/无版权（Tip/Ignore）直接终止记录，不进重试计数、不走上报。"""
    from nonebot_plugin_parser import exception as exc_mod

    exc = getattr(exc_mod, exc_name)("作品已下架")
    fr, calls = _patch_retry(monkeypatch, exc)

    await fr._retry_one({"url": "https://example.com/x", "retries": 0})

    assert calls == [("reported", "https://example.com/x")]


@pytest.mark.asyncio
async def test_l2_generic_exception_still_retries(monkeypatch):
    """普通异常（网络/结构改版）仍走重试状态机。"""
    fr, calls = _patch_retry(monkeypatch, RuntimeError("boom"))

    await fr._retry_one({"url": "https://example.com/y", "retries": 0})

    assert calls
    assert calls[0][0] == "retried"
    assert ("reported", "https://example.com/y") not in calls
