"""测试 YouTube 解析器。

YouTube 依赖 yt-dlp（可选 extras），未安装时跳过。
解析器本体此前零直接测试，此处覆盖：
- URL 匹配（短链 / watch / shorts）
- parse_video：标题、作者（youtubei browse API）、封面、视频/图片内容
"""

import pytest

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _ytdlp_available() -> bool:
    try:
        from nonebot_plugin_parser.download import YTDLP_DOWNLOADER

        return YTDLP_DOWNLOADER is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ytdlp_available(),
    reason="未安装 yt-dlp extras，跳过 YouTube 解析器测试",
)


def test_search_url_match():
    """测试各类 YouTube URL 都能被匹配"""
    from nonebot_plugin_parser.parsers import YouTubeParser

    parser = YouTubeParser()
    for url in [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abcd1234_-",
    ]:
        _, matched = parser.search_url(url)
        assert matched, f"应能匹配: {url}"


@pytest.mark.asyncio
async def test_parse_video():
    """测试解析视频：标题、作者、内容（实网请求，失败则跳过）"""
    from nonebot_plugin_parser.parsers import YouTubeParser

    parser = YouTubeParser()
    keyword, matched = parser.search_url(YOUTUBE_URL)
    assert matched

    try:
        result = await parser.parse(keyword, matched)
    except Exception as e:
        pytest.skip(f"YouTube 解析失败（可能网络/cookie 问题），跳过: {e!r}")

    assert result.title, "应能提取标题"
    assert result.author, "应能提取作者（youtubei browse API）"
    assert result.contents, "应有内容（视频或封面图）"
    assert result.timestamp is not None, "应能提取发布时间戳"
    assert result.platform.name == "youtube"
