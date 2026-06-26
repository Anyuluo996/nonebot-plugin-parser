"""测试 Pixiv R18/R-18G 拦截逻辑"""
import os

import pytest
from nonebot import logger

os.environ["FORCE_COLOR"] = "0"
os.environ["TERM"] = "dumb"


def _make_illust_body(x_restrict: int = 0) -> dict:
    return {
        "error": False,
        "body": {
            "illustId": "999999999",
            "title": "测试标题",
            "description": "测试描述",
            "user": {"userId": "12345", "userName": "测试作者"},
            "tags": {"tags": [{"tag": "R-18", "locked": False}]},
            "uploadDate": "2024-01-01T00:00:00+00:00",
            "xRestrict": x_restrict,
        },
    }


def _make_pages_body() -> list:
    return [
        {
            "urls": {
                "original": "https://i.pixiv.net/img-original/img/2024/01/01/00/00/00/999999999_p0.jpg"
            }
        }
    ]


class MockIllustResponse:
    """Mock /ajax/illust/{id} 响应"""

    def __init__(self, x_restrict: int):
        self._body = _make_illust_body(x_restrict)

    def raise_for_status(self):
        pass

    @property
    def content(self):
        import json

        return json.dumps(self._body).encode()


class MockPagesResponse:
    """Mock /ajax/illust/{id}/pages 响应"""

    def raise_for_status(self):
        pass

    @property
    def content(self):
        import json

        return json.dumps({"error": False, "body": _make_pages_body()}).encode()


def _make_mock_request(illust_resp: MockIllustResponse, pages_resp: MockPagesResponse):
    """构造返回预设响应的 mock ``request`` 回调。

    解析器重构后通过 ``BaseParser.request`` 统一发请求，测试改为 mock 该方法，
    依次返回 illust / pages 响应。
    """

    responses = [illust_resp, pages_resp]

    async def _fake_request(*args, **kwargs):
        return responses.pop(0)

    return _fake_request


@pytest.mark.asyncio
async def test_pixiv_r18_block_when_disabled():
    """测试 R18 (xRestrict=2) 内容在禁用时被拦截"""
    import unittest.mock as mock

    from nonebot_plugin_parser.parsers import PixivParser
    from nonebot_plugin_parser.exception import IgnoreException

    parser = PixivParser()
    url = "https://www.pixiv.net/artworks/999999999"
    keyword, searched = parser.search_url(url)
    assert searched

    logger.info("[R18=false] 测试拦截 xRestrict=2 的内容")

    illust_resp = MockIllustResponse(x_restrict=2)
    pages_resp = MockPagesResponse()

    with mock.patch.object(parser, "request", _make_mock_request(illust_resp, pages_resp)):
        with pytest.raises(IgnoreException) as exc_info:
            await parser.parse(keyword, searched)

    msg = str(exc_info.value)
    assert "R18" in msg or "par_pixivR18" in msg, f"错误信息不含 R18: {msg}"
    logger.info(f"OK: R18 内容被正确拦截 -> {exc_info.value}")


@pytest.mark.asyncio
async def test_pixiv_r18g_block_when_disabled():
    """测试 R-18G (xRestrict=1) 内容在禁用时被拦截"""
    import unittest.mock as mock

    from nonebot_plugin_parser.parsers import PixivParser
    from nonebot_plugin_parser.exception import IgnoreException

    parser = PixivParser()
    url = "https://www.pixiv.net/artworks/999999999"
    keyword, searched = parser.search_url(url)
    assert searched

    logger.info("[R18=false] 测试拦截 xRestrict=1 (R-18G) 的内容")

    illust_resp = MockIllustResponse(x_restrict=1)
    pages_resp = MockPagesResponse()

    with mock.patch.object(parser, "request", _make_mock_request(illust_resp, pages_resp)):
        with pytest.raises(IgnoreException) as exc_info:
            await parser.parse(keyword, searched)

    msg = str(exc_info.value)
    assert "R18" in msg or "par_pixivR18" in msg, f"错误信息不含 R18: {msg}"
    logger.info(f"OK: R-18G 内容被正确拦截 -> {exc_info.value}")


@pytest.mark.asyncio
async def test_pixiv_r18_allow_when_enabled():
    """测试 R18/R-18G 内容在启用时可以正常解析"""
    import unittest.mock as mock

    import nonebot_plugin_parser.config as config_module
    from nonebot_plugin_parser.parsers import PixivParser

    orig = config_module.pconfig.parser_pixivR18
    config_module.pconfig.parser_pixivR18 = True

    try:
        parser = PixivParser()
        url = "https://www.pixiv.net/artworks/999999999"
        keyword, searched = parser.search_url(url)
        assert searched

        logger.info("[R18=true] 测试解析 xRestrict=2 的内容")

        illust_resp = MockIllustResponse(x_restrict=2)
        pages_resp = MockPagesResponse()

        with mock.patch.object(parser, "request", _make_mock_request(illust_resp, pages_resp)):
            with mock.patch("nonebot_plugin_parser.download.DOWNLOADER") as mock_dl:
                mock_task = mock.MagicMock()
                mock_dl.download_img.return_value = mock_task

                result = await parser.parse(keyword, searched)

        assert result.title == "测试标题"
        assert result.author is not None
        assert result.author.name == "测试作者"
        assert len(result.img_contents) == 1
        logger.info(f"OK: R18 内容解析成功, title={result.title}, author={result.author.name}")
    finally:
        config_module.pconfig.parser_pixivR18 = orig
