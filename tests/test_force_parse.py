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
        PSR_FORCE_PARSE_KEY,
        PSR_SEARCHED_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", "")
    text = f"{pconfig.nickname}+https://www.bilibili.com/video/BV1xx411c7mD"
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))
    state = {}

    matched = await rule(_text_message(text), state)

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
        PSR_FORCE_PARSE_KEY,
        PSR_SEARCHED_KEY,
        KeyPatternList,
        KeywordRegexRule,
    )

    monkeypatch.setattr(pconfig, "parser_force_prefix", " bot ")
    rule = KeywordRegexRule(KeyPatternList(("bilibili", r"bilibili\.com/video/([A-Za-z0-9]+)")))
    state = {}

    matched = await rule(_text_message(text), state)

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


def test_is_enabled_all_disabled_but_force_prefix_still_allowed(monkeypatch):
    from nonebot_plugin_parser.constants import PlatformEnum
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT, get_group_key, is_enabled

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

    from nonebot_plugin_parser.constants import PlatformEnum
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.matchers import parser_handler
    from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT, get_group_key, is_enabled
    from nonebot_plugin_parser.matchers.rule import PSR_FORCE_PARSE_KEY, PSR_SEARCHED_KEY, KeyPatternList, KeywordRegexRule
    from nonebot_plugin_parser.parsers import ParseResult, Platform

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
        assert await rule(message, state) is True
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