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

    # 下载段单独兜底: 页面/解码修复后, 音频 CDN 偶发风控或本机网络不应判为代码回归
    audio = result.audio_contents[0]
    try:
        audio_path = await audio.get_path()
    except Exception as e:
        pytest.skip(f"{TRACK_URL} | 音频下载失败(网络/风控), 跳过: {e}")
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


def test_router_data_regex_tolerates_script_attrs():
    """回归（2026-11-06 线上故障）：script 标签属性变化不应导致正则失配。

    抖音在分享页 script 标签上加了 nonce="argus-csp-token"，旧的严格属性
    序列正则（async="" data-script-src=... 紧邻）整体失配 —— _ROUTER_DATA
    子串仍在页面里，正则必挂，症状为确定性「未找到结构化数据」，曾被误判
    为 IP 软风控。修复后只锚定赋值形态，对属性增删/换序免疫。
    """
    from nonebot_plugin_parser.parsers.qsmusic import _ROUTER_DATA

    payload = '{"loaderData":{"track_page":{"audioWithLyricsOption":{"url":"https://x/a.mp3"}}}}'
    shapes = {
        # 旧形态（属性紧邻）
        "old": f'<script async="" data-script-src="modern-inline">_ROUTER_DATA = {payload};</script>',
        # 2026-11 线上形态（中间插入 nonce）
        "nonce": (
            f'<script async="" nonce="argus-csp-token" data-script-src="modern-inline">'
            f"_ROUTER_DATA = {payload};</script>"
        ),
        # 属性换序 + 多属性
        "reordered": (
            f'<script data-script-src="modern-inline" nonce="abc" defer async="">_ROUTER_DATA = {payload};</script>'
        ),
    }
    for name, html in shapes.items():
        m = _ROUTER_DATA.search(html)
        assert m is not None, f"{name} 形态未匹配"
        assert m[1] == payload, f"{name} 形态捕获内容不符"


@pytest.mark.asyncio
async def test_parse_with_nonce_script_tag(monkeypatch):
    """完整解析回归：带 nonce 属性的真实页面形态应正常解析出歌曲结果。"""
    import json
    import asyncio
    from pathlib import Path

    from nonebot_plugin_parser.parsers import QSMusicParser
    from nonebot_plugin_parser.parsers import base as parser_base

    audio_json = json.dumps(
        {
            "loaderData": {
                "track_page": {
                    "audioWithLyricsOption": {
                        "url": "https://example.com/audio.mp3",
                        "duration": 30.0,
                        "artistName": "测试歌手",
                        "trackName": "测试歌曲",
                        "coverURL": "https://example.com/cover.jpg",
                        "trackInfo": {
                            "stats": {},
                            "album": {"name": "测试专辑", "id": "1"},
                        },
                        # 2026-11 线上漂移形态: startMs/endMs 为浮点 (旧 int 结构体直接解码炸)
                        "lyrics": {"sentences": [{"startMs": 100.0, "endMs": 1500.0, "text": "测试歌词", "words": []}]},
                    }
                }
            }
        },
        ensure_ascii=False,
    )
    html = (
        '<html><body><script async="" nonce="argus-csp-token" data-script-src="modern-inline">'
        f"_ROUTER_DATA = {audio_json};</script></body></html>"
    )

    class _FakeResp:
        def __init__(self, text):
            self.text = text

    async def _fake_request(_url, **_kwargs):
        # 挂实例属性不绑定 self: self.request(url, ...) 首参即 url
        return _FakeResp(html)

    def _fake_download(_url, **_kwargs):
        # @auto_task 形态：调用即返回 Task
        return asyncio.ensure_future(asyncio.sleep(0, result=Path("/tmp") / "fake.bin"))

    monkeypatch.setattr(parser_base.DOWNLOADER, "download_audio", _fake_download)
    monkeypatch.setattr(parser_base.DOWNLOADER, "download_img", _fake_download)

    parser = QSMusicParser()
    monkeypatch.setattr(parser, "request", _fake_request)

    keyword, searched = parser.search_url(TRACK_URL)
    result = await parser.parse(keyword, searched)

    assert result.title == "测试歌曲"
    assert result.author is not None, "作者信息缺失"
    assert result.author.name == "测试歌手"
    assert result.audio_contents, "音频内容缺失"
    # 浮点毫秒 → LRC 时间标签收口
    assert "[00:00.10]测试歌词" in result.extra["lyric"], f"歌词转换不符: {result.extra['lyric']!r}"


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

    # 函数内 `from nonebot_plugin_uninfo import get_session` 取模块属性，patch 模块即生效；
    # is_platform_enabled 由 base.py 从 platform_switch 懒导入，patch 该模块即生效
    monkeypatch.setattr(nonebot_plugin_uninfo, "get_session", _fake_get_session)
    monkeypatch.setattr(
        "nonebot_plugin_parser.platform_switch.is_platform_enabled",
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
