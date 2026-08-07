import pytest
from nonebot import logger

# 线上回归链接: 抖音短链 v.douyin.com/wX71xwbWuDM 重定向至此 track 页。
# 旧版 qishui.douyin.com/s/xxx/ 格式线上已不再被 app 分享，此处仅覆盖当前
# 实际落点 music.douyin.com/qishui/share/track?track_id=...
TRACK_URL = "https://music.douyin.com/qishui/share/track?track_id=7670742106552256539&from_item_id=7671065299874206569"

# v.douyin.com 短链，验证 DouyinParser.parse_with_redirect 跨 parser 路由到汽水。
# 会被抖音侧重定向到上面的 TRACK_URL（末尾 query 可能随时间变化，但路径稳定）。
SHORT_URL = "https://v.douyin.com/wX71xwbWuDM"


def _ensure_parser_registry():
    """填充 KEYWORD_PARSER_MAP（跨 parser 路由依赖）。

    conftest.init_nonebot 只 nonebot.init() + load_from_toml，不触发 driver.run()，
    故 @get_driver().on_startup 注册的 register_parser_matcher 不执行，map 为空。
    幂等：已填充则直接返回。
    """
    from nonebot_plugin_parser.matchers import KEYWORD_PARSER_MAP, register_parser_matcher

    if not KEYWORD_PARSER_MAP:
        register_parser_matcher()


def test_qsmusic_search_url():
    """直接汽水 track 链接应被 QSMusicParser 匹配到（不含则跨 parser 无法命中）。"""
    from nonebot_plugin_parser.parsers import QSMusicParser

    parser = QSMusicParser()
    keyword, searched = parser.search_url(TRACK_URL)
    assert keyword == "music.douyin.com"
    assert "qishui/share/track" in searched.group(0)


@pytest.mark.asyncio
async def test_qsmusic_parse():
    """解析汽水音乐 track 页，校验标题/作者/音频/歌词。"""
    from nonebot_plugin_parser.parsers import QSMusicParser

    parser = QSMusicParser()
    keyword, searched = parser.search_url(TRACK_URL)

    logger.info(f"{TRACK_URL} | 开始解析汽水音乐")
    try:
        result = await parser.parse(keyword, searched)
    except Exception as e:
        pytest.skip(f"{TRACK_URL} | 链接失效或被风控，跳过: {e}")

    logger.debug(f"{TRACK_URL} | 解析结果:\n{result}")

    assert result.title, "歌曲标题为空"
    assert result.author, "作者信息为空"
    assert result.audio_contents, "音频内容为空"

    audio = result.audio_contents[0]
    audio_path = await audio.get_path()
    assert audio_path.exists(), "音频下载失败"
    logger.success(f"{TRACK_URL} | 汽水音乐解析成功: {result.title} - {result.author}")


@pytest.mark.asyncio
async def test_douyin_short_link_redirect_to_qsmusic():
    """抖音短链重定向到汽水 track 页时，应跨 parser 路由到 QSMusicParser。

    覆盖线上报错：v.douyin.com/wX71xwbWuDM 重定向到 music.douyin.com/qishui/...
    后 DouyinParser.search_url 无法匹配，旧实现抛 ParseException。
    """
    _ensure_parser_registry()
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.matchers import get_parser_by_type

    # 用生产实例（KEYWORD_PARSER_MAP 里的），使 base.parse_with_redirect 的
    # `parser is self` 跳过路径被真实走到（自建实例是不同对象，跳不到）。
    parser = get_parser_by_type(DouyinParser)
    keyword, searched = parser.search_url(SHORT_URL)
    assert searched, "抖音短链未被匹配"

    try:
        result = await parser.parse(keyword, searched)
    except Exception as e:
        pytest.skip(f"{SHORT_URL} | 链接失效或被风控，跳过: {e}")

    # 跨 parser 路由后，结果应来自汽水音乐平台
    assert result.platform.name == "qsmusic", f"短链未跨 parser 路由到汽水音乐，实际平台: {result.platform.name}"
    assert result.audio_contents, "音频内容为空"
    logger.success(f"{SHORT_URL} | 抖音短链跨 parser 路由到汽水音乐成功: {result.title}")


@pytest.mark.asyncio
async def test_cross_parser_route_respects_disabled_platform(monkeypatch):
    """跨 parser 路由命中目标平台后，若该平台在当前会话被禁用，应跳过不解析。

    覆盖鉴权：管理员关闭了汽水音乐，但抖音短链 redirect 到汽水 → 不应绕过关闭设定。
    通过 monkeypatch 模拟 _is_platform_allowed 返回 False（等价于群组禁用了 qsmusic），
    验证 parse_with_redirect 跳过目标 parser 而非继续解析。
    """
    import nonebot_plugin_parser.parsers.base as base_mod

    # 记录被查询过的平台，验证鉴权检查确实针对 qsmusic 发生
    checked_platforms: list[str] = []

    async def _fake_check(platform_name: str) -> bool:
        checked_platforms.append(platform_name)
        # qsmusic 被禁用，其它平台放行
        return platform_name != "qsmusic"

    monkeypatch.setattr(base_mod, "_is_platform_allowed", _fake_check)

    _ensure_parser_registry()
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.matchers import get_parser_by_type
    from nonebot_plugin_parser.exception import ParseException

    parser = get_parser_by_type(DouyinParser)
    keyword, searched = parser.search_url(SHORT_URL)

    with pytest.raises(ParseException, match="无法匹配"):
        await parser.parse(keyword, searched)

    # 确认鉴权检查被调用，且针对的是汽水音乐平台
    assert "qsmusic" in checked_platforms, "跨 parser 路由未对汽水音乐平台做鉴权检查"
    logger.success(f"{SHORT_URL} | 目标平台被禁用时正确跳过，鉴权检查覆盖平台: {checked_platforms}")


@pytest.mark.asyncio
async def test_is_platform_allowed_no_context():
    """_is_platform_allowed 在非 matcher 运行上下文（无 current_bot/event）时放行。

    覆盖测试直调、定时任务等场景：拿不到 ContextVar 不应阻断解析。
    """
    from nonebot_plugin_parser.parsers.base import _is_platform_allowed

    # 测试环境未设置 current_bot/current_event，应返回 True
    assert await _is_platform_allowed("qsmusic") is True


@pytest.mark.asyncio
async def test_is_platform_allowed_with_session_and_disabled(monkeypatch):
    """_is_platform_allowed 在 matcher 上下文中委托 is_platform_enabled 的真实判定。

    设置 current_bot/current_event ContextVar + mock get_session，
    验证：平台被禁用返回 False；get_session 抛 I/O 异常时降级放行 True。
    """
    import nonebot_plugin_uninfo
    from nonebot.matcher import current_bot, current_event

    from nonebot_plugin_parser.parsers.base import _is_platform_allowed

    fake_bot = object()
    fake_event = object()
    token_bot = current_bot.set(fake_bot)  # type: ignore[arg-type]
    token_event = current_event.set(fake_event)  # type: ignore[arg-type]

    called: list[str] = []

    async def _fake_get_session(bot, event):
        return object()  # sentinel session，is_platform_enabled 也被 mock 不会真用

    def _fake_is_platform_enabled(session, name):
        called.append(name)
        return False  # 模拟平台被禁用

    # 函数内 `from nonebot_plugin_uninfo import get_session` 取模块属性，patch 模块即生效
    monkeypatch.setattr(nonebot_plugin_uninfo, "get_session", _fake_get_session)
    monkeypatch.setattr(
        "nonebot_plugin_parser.matchers.filter.is_platform_enabled",
        _fake_is_platform_enabled,
    )

    try:
        # 1. 平台被禁用 → 返回 False
        assert await _is_platform_allowed("qsmusic") is False
        assert called == ["qsmusic"], f"应调用 is_platform_enabled('qsmusic'), 实际: {called}"

        # 2. get_session 抛异常 → 降级放行 True
        called.clear()

        async def _failing_get_session(bot, event):
            raise RuntimeError("adapter I/O failed")

        monkeypatch.setattr(nonebot_plugin_uninfo, "get_session", _failing_get_session)
        assert await _is_platform_allowed("qsmusic") is True
    finally:
        current_bot.reset(token_bot)
        current_event.reset(token_event)
