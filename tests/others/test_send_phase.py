"""发送段失败处理单元测试。

对应 2026-09-27 yun nb2 排查: NapCat NTQQ sendMsg 回调假超时
(retcode=1200, EventRet result=0, 消息实际已发出)曾被统一误标
「解析失败」, 且单节点消息无重试。覆盖:
- 单节点消息发送失败重试一次, 二次成功不报错
- 二次仍失败则上抛, 只重试一次
- 合并转发失败维持降级逐条直发, 不重试转发整体
- parser_handler 发送段失败以「发送段」前缀记入 failure_store 且不再冒泡

注意: nonebot_plugin_parser.* 的 import 必须放在测试函数内部,
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import re

import pytest


class _FlakyMessage:
    """send 前 ``failures`` 次抛指定异常, 之后成功。"""

    def __init__(self, failures: int = 0, exc_factory=None):
        self.send_calls = 0
        self._failures = failures
        self._exc_factory = exc_factory or (lambda: RuntimeError("send failed"))

    async def send(self):
        self.send_calls += 1
        if self.send_calls <= self._failures:
            raise self._exc_factory()


class _StubRenderer:
    def __init__(self, messages):
        self._messages = messages

    async def render_messages(self, result):
        for message in self._messages:
            yield message


def _napcat_1200():
    """构造与真实 NapCat sendMsg 假超时同形的 ActionFailed。"""
    from nonebot.adapters.onebot.v11.exception import ActionFailed

    return ActionFailed(
        status="failed",
        retcode=1200,
        data=None,
        message="Timeout: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg",
        wording="Timeout",
        echo="74",
    )


def _stub_matchers(monkeypatch, messages):
    """替换 renderer 与 UniHelper, 返回 matchers 模块。

    UniHelper.extract_forward_nodes 对非 Reference 消息原样返回 [message]
    (单节点), 与真实行为一致; 合并转发场景在用例内另行覆盖。
    重试等待置 0, 用例不等真实 3s。
    """
    from nonebot_plugin_parser import matchers as matchers_mod

    class _StubUniHelper:
        @staticmethod
        def extract_forward_nodes(message):
            return [message]

    monkeypatch.setattr(matchers_mod, "get_renderer", lambda name: _StubRenderer(messages))
    monkeypatch.setattr(matchers_mod, "UniHelper", _StubUniHelper)
    monkeypatch.setattr(matchers_mod, "_SEND_RETRY_DELAY", 0)
    return matchers_mod


def _fake_result():
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="douyin", display_name="抖音"),
        author=Author(name="test"),
        title="t",
        url="https://example.com",
        contents=[],
        graphics=[],
    )


@pytest.mark.asyncio
async def test_single_node_send_retries_once_then_succeeds(monkeypatch):
    """首次发送失败(假超时), 重试一次成功, 不向调用方抛错。"""
    msg = _FlakyMessage(failures=1, exc_factory=_napcat_1200)
    _stub_matchers(monkeypatch, [msg])

    from nonebot_plugin_parser.matchers import _send_parse_result

    await _send_parse_result(_fake_result())
    assert msg.send_calls == 2, f"应恰好发送 2 次(首次+重试), 实际 {msg.send_calls}"


@pytest.mark.asyncio
async def test_single_node_send_retry_exhausted_raises(monkeypatch):
    """二次仍失败则上抛, 且只重试一次不无限重发。"""
    msg = _FlakyMessage(failures=99, exc_factory=_napcat_1200)
    _stub_matchers(monkeypatch, [msg])

    from nonebot.adapters.onebot.v11.exception import ActionFailed

    from nonebot_plugin_parser.matchers import _send_parse_result

    with pytest.raises(ActionFailed):
        await _send_parse_result(_fake_result())
    assert msg.send_calls == 2, f"应恰好发送 2 次, 实际 {msg.send_calls}"


@pytest.mark.asyncio
async def test_forward_failure_degrades_without_retrying_whole(monkeypatch):
    """合并转发失败走降级逐条直发, 不重试转发整体(整包重复风险)。"""
    ref_msg = _FlakyMessage(failures=99, exc_factory=_napcat_1200)
    node1, node2 = _FlakyMessage(), _FlakyMessage()
    matchers_mod = _stub_matchers(monkeypatch, [ref_msg])

    class _StubForwardUniHelper:
        @staticmethod
        def extract_forward_nodes(_message):
            return [node1, node2]

    monkeypatch.setattr(matchers_mod, "UniHelper", _StubForwardUniHelper)

    from nonebot_plugin_parser.matchers import _send_parse_result

    await _send_parse_result(_fake_result())
    assert ref_msg.send_calls == 1, "转发整体不应重试"
    assert node1.send_calls == 1, "节点1应直发一次"
    assert node2.send_calls == 1, "节点2应直发一次"


@pytest.mark.asyncio
async def test_parser_handler_send_failure_recorded_as_send_phase(monkeypatch):
    """发送段失败以「发送段」前缀记入 failure_store, 不再误标/冒泡为解析失败。"""
    from nonebot.matcher import current_event

    from nonebot_plugin_parser.parsers import Platform
    from nonebot_plugin_parser.matchers import parser_handler
    from nonebot_plugin_parser.matchers.rule import SearchResult

    class MockScene:
        is_private = False

    class MockUser:
        id = "88888"

    class MockSession:
        scene = MockScene()
        scope = "qq"
        scene_path = "send-phase-test-group"
        user = MockUser()

    class FakeParser:
        platform = Platform(name="bilibili", display_name="哔哩哔哩")

    matchers_mod = _stub_matchers(monkeypatch, [])

    async def fake_parse(_parser, _keyword, _searched):
        return _fake_result()

    async def fake_send(_result):
        raise _napcat_1200()

    recorded: dict = {}

    def fake_record_failure(url: str, platform: str, error: str):
        recorded.update(url=url, platform=platform, error=error)

    async def fake_reaction(*_args, **_kwargs):
        return None

    monkeypatch.setattr(matchers_mod, "parse_with_retry", fake_parse)
    monkeypatch.setattr(matchers_mod, "_send_parse_result", fake_send)
    monkeypatch.setattr(matchers_mod, "record_failure", fake_record_failure)
    monkeypatch.setattr(matchers_mod, "get_parser", lambda keyword: FakeParser())
    monkeypatch.setattr("nonebot_plugin_parser.helper.UniHelper.message_reaction", fake_reaction)

    text = "https://www.bilibili.com/video/BV1xx411c7mD"
    sr = SearchResult(
        text=text,
        keyword="bilibili",
        searched=re.search(r"bilibili\.com/video/(BV[ A-Za-z0-9]+)", text),
    )
    token = current_event.set(object())
    try:
        # 不抛: 发送段失败由 handler 收口记录
        await parser_handler(sr, MockSession(), {})
    finally:
        current_event.reset(token)

    assert recorded.get("platform") == "bilibili"
    assert recorded.get("error", "").startswith("发送段"), f"应以「发送段」标记, 实际 {recorded.get('error')!r}"
