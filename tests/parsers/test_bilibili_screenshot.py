"""B站短链重定向到无 handler 页面时浏览器截图兜底的回归测试"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_short_link_screenshot_fallback(monkeypatch):
    """重定向到会员购商城等无 handler 页面时，应走浏览器截图兜底"""
    import nonebot_plugin_parser.browser as browser_mod
    from nonebot_plugin_parser.parsers import BilibiliParser
    from nonebot_plugin_parser.parsers.data import ImageContent

    parser = BilibiliParser()
    _, searched = parser.search_url("b23.tv/MyxuBs8")

    mall_url = (
        "https://mall.bilibili.com/neul-next/detailuniversal/detail.html"
        "?itemsId=13656308&page=detailuniversal_detail"
    )

    monkeypatch.setattr(parser, "get_redirect_url", AsyncMock(return_value=mall_url))
    monkeypatch.setattr(browser_mod, "is_browser_available", lambda: True)
    fake_path = Path("fake_screenshot.png")
    monkeypatch.setattr(
        browser_mod,
        "screenshot_url",
        AsyncMock(return_value=(fake_path, "测试商品")),
    )

    result = await parser._parse_short_link(searched)

    assert result.url == mall_url
    assert result.title == "测试商品"
    assert len(result.contents) == 1
    assert isinstance(result.contents[0], ImageContent)
    assert result.contents[0].path_task == fake_path
    assert result.extra.get("content_type") == "网页截图"


@pytest.mark.asyncio
async def test_short_link_screenshot_disabled(monkeypatch):
    """parser_screenshot=False 时，无 handler 应保留原 ParseException 行为"""
    import nonebot_plugin_parser.browser as browser_mod
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import BilibiliParser
    from nonebot_plugin_parser.exception import ParseException

    parser = BilibiliParser()
    _, searched = parser.search_url("b23.tv/MyxuBs8")
    mall_url = "https://mall.bilibili.com/neul-next/detailuniversal/detail.html?itemsId=13656308"

    monkeypatch.setattr(parser, "get_redirect_url", AsyncMock(return_value=mall_url))
    monkeypatch.setattr(pconfig, "parser_screenshot", False)
    screenshot_mock = AsyncMock()
    monkeypatch.setattr(browser_mod, "screenshot_url", screenshot_mock)

    with pytest.raises(ParseException) as exc:
        await parser._parse_short_link(searched)
    assert "无法匹配" in exc.value.message
    screenshot_mock.assert_not_called()


@pytest.mark.asyncio
async def test_short_link_screenshot_unavailable(monkeypatch):
    """未安装 htmlrender 时，无 handler 应抛 TipException（含安装提示）"""
    import nonebot_plugin_parser.browser as browser_mod
    from nonebot_plugin_parser.parsers import BilibiliParser
    from nonebot_plugin_parser.exception import TipException

    parser = BilibiliParser()
    _, searched = parser.search_url("b23.tv/MyxuBs8")
    mall_url = "https://mall.bilibili.com/neul-next/detailuniversal/detail.html?itemsId=13656308"

    monkeypatch.setattr(parser, "get_redirect_url", AsyncMock(return_value=mall_url))
    monkeypatch.setattr(browser_mod, "is_browser_available", lambda: False)

    with pytest.raises(TipException) as exc:
        await parser._parse_short_link(searched)
    assert "htmlrender" in exc.value.message


@pytest.mark.asyncio
async def test_short_link_normal_redirect_not_screenshot(monkeypatch):
    """重定向到可解析页面（BV）时，应走正常 parse 路径，不触发截图"""
    import nonebot_plugin_parser.browser as browser_mod
    from nonebot_plugin_parser.parsers import BilibiliParser

    parser = BilibiliParser()
    _, searched = parser.search_url("b23.tv/MyxuBs8")
    bv_url = "https://www.bilibili.com/video/BV1xx411c7mD"

    monkeypatch.setattr(parser, "get_redirect_url", AsyncMock(return_value=bv_url))

    called = {}

    async def fake_parse(keyword, searched_new):
        called["keyword"] = keyword
        return parser.result(title="fake video")

    monkeypatch.setattr(parser, "parse", fake_parse)
    screenshot_mock = AsyncMock()
    monkeypatch.setattr(browser_mod, "screenshot_url", screenshot_mock)

    result = await parser._parse_short_link(searched)
    assert called.get("keyword") == "/BV"
    assert result.title == "fake video"
    screenshot_mock.assert_not_called()
