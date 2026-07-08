import pytest


def _text_message(text: str):
    from nonebot_plugin_alconna.uniseg import Text, UniMessage

    return UniMessage([Text(text)])


def test_config_parse_prefix_requires_explicit_setting():
    from nonebot_plugin_parser.config import Config

    assert Config().parse_prefix == ""
    assert Config(parser_force_prefix="  bot  ").parse_prefix == "bot"


@pytest.mark.asyncio
async def test_keyword_regex_rule_does_not_use_nickname_as_default_prefix(monkeypatch):
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.rule import (
        PSR_SEARCHED_KEY,
        PSR_FORCE_PARSE_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", "")
    text = f"{pconfig.nickname}+https://www.bilibili.com/video/BV1xx411c7mD"
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))
    state = {}

    matched = await rule(_text_message(text), _FakeEvent(None), state)

    assert matched is True
    assert state[PSR_FORCE_PARSE_KEY] is False
    assert state[PSR_SEARCHED_KEY].text == text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "bot+ https://www.bilibili.com/video/BV1xx411c7mD",
        "bot https://www.bilibili.com/video/BV1xx411c7mD",
    ],
)
async def test_keyword_regex_rule_force_parse_with_explicit_prefix(monkeypatch, text: str):
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.rule import (
        PSR_SEARCHED_KEY,
        PSR_FORCE_PARSE_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", " bot ")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))
    state = {}

    matched = await rule(_text_message(text), _FakeEvent(None), state)

    assert matched is True
    assert state[PSR_FORCE_PARSE_KEY] is True
    assert state[PSR_SEARCHED_KEY].text == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_bilibili_parser_initializes_base_fields():
    from nonebot_plugin_parser.parsers import BilibiliParser

    parser = BilibiliParser()

    assert parser.timeout is not None
    assert parser.ios_headers
    assert parser.android_headers
    assert parser.headers["Referer"].rstrip("/") == "https://www.bilibili.com"


def test_parser_handler_state_param_is_injected_by_nonebot_di():
    """回归测试: parser_handler 的 state 参数必须被 NoneBot DI 注入。

    之前 ``state: T_State | None = None`` 让 Union 类型绕过了
    StateParam._check_param (它只认直接的 T_State/Annotated), 导致
    state 始终为 None, force_parse 永远 False, 前缀强制解析失效。
    正确注解下 NoneBot 会把它解析成 StateParam。
    """
    import inspect

    from nonebot.internal.params import StateParam

    from nonebot_plugin_parser.matchers import parser_handler

    sig = inspect.signature(parser_handler)
    state_param = sig.parameters["state"]

    # 用 NoneBot 的 StateParam 解析器逐参校验, 它应返回 StateParam 实例
    resolved = StateParam._check_param(state_param, ())
    assert resolved is not None, (
        f"state 参数未被 NoneBot DI 识别为 StateParam (annotation={state_param.annotation!r}); "
        "若用 T_State | None 会丢失 StateFlag, 导致 force_parse 永远 False, 前缀强制解析失效"
    )


# === 引用回复强制解析测试 ===


class _FakeReplyMessage:
    """模拟 OneBot v11 event.reply.message (有 extract_plain_text)。"""

    def __init__(self, text: str):
        self._text = text

    def extract_plain_text(self) -> str:
        return self._text


class _FakeReply:
    """模拟 OneBot v11 event.reply。"""

    def __init__(self, text: str):
        self.message = _FakeReplyMessage(text)
        self.message_id = 12345


class _FakeEvent:
    """模拟 OneBot v11 event (有 reply 属性)。

    通过 DI 注入到 rule.__call__ 的 event 参数 (而非 current_event ContextVar),
    因为 NoneBot 规则在 ensure_context 之前执行, 此时 current_event 尚未设置。
    """

    def __init__(self, reply_text: str | None):
        self.reply = _FakeReply(reply_text) if reply_text is not None else None


@pytest.mark.asyncio
async def test_force_prefix_reply_extracts_url_from_quoted_message(monkeypatch):
    """回归: 回复含 URL 的消息 + 只输入前缀 'par', 从被引用消息提取 URL 强制解析。

    OneBot v11 adapter 在分发前已把 reply 段从 event.message 删除并填充
    event.reply.message (含被引用消息完整内容), 故用户只输入 'par' 时 event.message
    无 URL, 需从 event.reply.message.extract_plain_text() 提取。

    event 由 NoneBot DI 注入到 rule.__call__ 的 event 参数 (非 current_event,
    因规则在 ensure_context 之前执行, current_event 此时尚未设置)。
    """
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.rule import (
        PSR_SEARCHED_KEY,
        PSR_FORCE_PARSE_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", "par")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))

    # 用户只输入 'par', 被引用消息含 bilibili URL
    msg = _text_message("par")
    state = {}
    event = _FakeEvent("https://www.bilibili.com/video/BV1xx411c7mD")
    matched = await rule(msg, event, state)

    assert matched is True, "回复含 URL 的消息 + 前缀应匹配"
    assert state[PSR_FORCE_PARSE_KEY] is True
    assert state[PSR_SEARCHED_KEY].text == "https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.mark.asyncio
async def test_force_prefix_reply_no_url_does_not_match(monkeypatch):
    """回归: 回复无URL 的消息 + 只输入前缀, 不应误匹配。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.rule import (
        PSR_FORCE_PARSE_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", "par")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))

    msg = _text_message("par")
    state = {}
    event = _FakeEvent("你好,这是一条普通消息")
    matched = await rule(msg, event, state)

    assert matched is False, "被引用消息无 URL 不应匹配"
    assert state[PSR_FORCE_PARSE_KEY] is True  # 前缀仍被识别


@pytest.mark.asyncio
async def test_force_prefix_no_reply_does_not_match(monkeypatch):
    """回归: 只输入前缀但无引用回复, 不应匹配 (没有 URL 来源)。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.rule import (
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", "par")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))

    msg = _text_message("par")
    state = {}
    event = _FakeEvent(None)
    matched = await rule(msg, event, state)

    assert matched is False, "无引用回复时纯前缀不应匹配"


@pytest.mark.asyncio
async def test_force_prefix_reply_works_without_current_event_set(monkeypatch):
    """回归: 规则执行期间 current_event 未设置时引用回复仍可工作。

    NoneBot 规则在 _check_matcher → check_rule 中执行, 早于 _run_matcher →
    ensure_context 设置 current_event。故 rule.__call__ 不能依赖 current_event
    ContextVar (会 LookupError), 必须用 NoneBot DI 注入的 event 参数。
    本测试显式不设置 current_event, 断言修复后路径仍能提取被引用 URL。
    """
    from nonebot.matcher import current_event

    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.rule import (
        PSR_SEARCHED_KEY,
        PSR_FORCE_PARSE_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    # 确认测试起点 current_event 未被设置 (模拟规则阶段真实状态)
    try:
        current_event.get()
        raise AssertionError("测试前置失败: current_event 不应已设置")
    except LookupError:
        pass

    monkeypatch.setattr(pconfig, "parser_force_prefix", "par")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))

    msg = _text_message("par")
    state = {}
    event = _FakeEvent("https://www.bilibili.com/video/BV1xx411c7mD")
    matched = await rule(msg, event, state)

    assert matched is True, "current_event 未设置时, DI 注入的 event 仍应能提取被引用 URL"
    assert state[PSR_FORCE_PARSE_KEY] is True
    assert state[PSR_SEARCHED_KEY].text == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_is_enabled_all_disabled_but_force_prefix_still_allowed(monkeypatch):
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.constants import PlatformEnum
    from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT, is_enabled, get_group_key

    class MockScene:
        is_private = False

    class MockSession:
        scene = MockScene()
        scope = "qq"
        scene_path = "force-parse-group"

    session = MockSession()
    group_key = get_group_key(session)
    original_disabled = _DISABLED_PLATFORMS_DICT.get(group_key)
    monkeypatch.setattr(pconfig, "parser_force_prefix", "bot")
    _DISABLED_PLATFORMS_DICT[group_key] = {platform.value for platform in PlatformEnum}

    try:
        assert is_enabled(_text_message("bot https://www.bilibili.com/video/BV1xx411c7mD"), session) is True
        assert is_enabled(_text_message("https://www.bilibili.com/video/BV1xx411c7mD"), session) is False
    finally:
        if original_disabled is None:
            _DISABLED_PLATFORMS_DICT.pop(group_key, None)
        else:
            _DISABLED_PLATFORMS_DICT[group_key] = original_disabled


@pytest.mark.asyncio
async def test_force_prefix_still_reaches_parser_handler_when_all_platforms_disabled(monkeypatch):
    from nonebot.matcher import current_event

    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import Platform, ParseResult
    from nonebot_plugin_parser.matchers import parser_handler
    from nonebot_plugin_parser.constants import PlatformEnum
    from nonebot_plugin_parser.matchers.rule import (
        PSR_SEARCHED_KEY,
        PSR_FORCE_PARSE_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )
    from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT, is_enabled, get_group_key

    class MockScene:
        is_private = False

    class MockSession:
        scene = MockScene()
        scope = "qq"
        scene_path = "force-handler-group"

    class FakeParser:
        platform = Platform(name="bilibili", display_name="哔哩哔哩")

        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def parse(self, keyword: str, searched):
            self.calls.append((keyword, searched.group(0)))
            return ParseResult(platform=self.platform, title="ok")

    class FakeMessage:
        def __init__(self, sent: list[str]):
            self.sent = sent

        async def send(self):
            self.sent.append("sent")

    class FakeRenderer:
        def __init__(self, sent: list[str]):
            self.sent = sent
            self.results: list[ParseResult] = []

        async def render_messages(self, result: ParseResult):
            self.results.append(result)
            yield FakeMessage(self.sent)

    session = MockSession()
    group_key = get_group_key(session)
    original_disabled = _DISABLED_PLATFORMS_DICT.get(group_key)
    fake_parser = FakeParser()
    sent: list[str] = []
    fake_renderer = FakeRenderer(sent)

    async def fake_reaction(*args, **kwargs):
        return None

    monkeypatch.setattr(pconfig, "parser_force_prefix", "bot")
    monkeypatch.setattr("nonebot_plugin_parser.matchers.get_parser", lambda keyword: fake_parser)
    monkeypatch.setattr("nonebot_plugin_parser.matchers.get_renderer", lambda platform_name: fake_renderer)
    monkeypatch.setattr("nonebot_plugin_parser.helper.UniHelper.message_reaction", fake_reaction)
    _DISABLED_PLATFORMS_DICT[group_key] = {platform.value for platform in PlatformEnum}

    message = _text_message("bot https://www.bilibili.com/video/BV1xx411c7mD")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))
    state = {}

    token = current_event.set(object())
    try:
        assert is_enabled(message, session) is True
        assert await rule(message, _FakeEvent(None), state) is True
        assert state[PSR_FORCE_PARSE_KEY] is True
        await parser_handler(state[PSR_SEARCHED_KEY], session, state)
    finally:
        current_event.reset(token)
        if original_disabled is None:
            _DISABLED_PLATFORMS_DICT.pop(group_key, None)
        else:
            _DISABLED_PLATFORMS_DICT[group_key] = original_disabled

    assert fake_parser.calls == [("bilibili", "bilibili.com/video/BV1xx411c7mD")]
    assert sent == ["sent"]
    assert len(fake_renderer.results) == 1
