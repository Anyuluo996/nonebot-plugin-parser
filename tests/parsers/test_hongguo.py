"""红果短剧 parser 真实网络测试（tests/parsers/ 自动带 network marker）。"""

import asyncio

import httpx
import pytest
from nonebot import logger


def _cancel_pending(result) -> None:
    """解析返回的延迟下载任务在断言结束后取消，避免 un-awaited 告警。"""
    for content in result.contents:
        task = getattr(content, "path_task", None)
        if asyncio.isfuture(task) and not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_share_short_link():
    """App 分享短链（真人剧，schemeParams 带 series_id + vid）。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    url = "https://novelquickapp.com/s/FO_dOvfhq54/"
    keyword, searched = parser.search_url(url)
    assert searched

    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")

    try:
        logger.info(f"解析结果: {result}")
        assert result.title, "标题为空"
        assert result.video_contents, "应包含视频内容"
        assert result.url == url
    finally:
        _cancel_pending(result)


@pytest.mark.asyncio
async def test_web_detail_link():
    """网页版 detail 链接（默认取第 1 集）。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    url = "https://hongguoduanju.com/detail?series_id=7689336045552274457"
    keyword, searched = parser.search_url(url)

    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")

    try:
        logger.info(f"解析结果: {result}")
        assert "人在诡异" in (result.title or ""), f"标题异常: {result.title}"
        assert result.video_contents, "应包含视频内容"
    finally:
        _cancel_pending(result)


@pytest.mark.asyncio
async def test_share_text_only():
    """纯文本分享（无链接）→ 站内搜索 → 第 1 集。"""
    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    text = "漫剧《人在诡异，靠第四天灾登顶至高》 - 免费好剧，尽在红果"
    keyword, searched = parser.search_url(text)

    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")

    try:
        assert result.title, "标题为空"
        assert result.video_contents, "应包含视频内容"
    finally:
        _cancel_pending(result)


@pytest.mark.asyncio
async def test_full_episode_via_app_api():
    """非游客可看分集（第 51 集）经 App 接口 + CENC 解密拿到可播放 mp4。

    无 skip 降级（解密链路是本平台的核心路径，需要持续可见），
    但网络层异常仍跳过。
    """
    from nonebot_plugin_parser.utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg 不可用，跳过解密链路验证")

    from nonebot_plugin_parser.parsers import HongguoParser

    parser = HongguoParser()
    url = "https://hongguoduanju.com/player/7689336045552274457/7689337825312918553"
    keyword, searched = parser.search_url(url)
    try:
        result = await parser.parse(keyword, searched)
    except httpx.HTTPError as e:
        pytest.skip(f"网络异常: {e!r}")
    try:
        assert result.video_contents
        path = await result.video_contents[0].get_path()
        assert path.exists()
        assert path.stat().st_size > 0
    finally:
        _cancel_pending(result)
