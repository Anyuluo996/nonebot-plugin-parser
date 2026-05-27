import asyncio

import pytest
from nonebot import logger


@pytest.mark.asyncio
async def test_live():
    logger.info("开始解析B站直播 https://live.bilibili.com/6")
    from nonebot_plugin_parser.parsers import BilibiliParser

    url = "https://live.bilibili.com/1"
    parser = BilibiliParser()
    _, searched = parser.search_url(url)
    room_id = int(searched.group("room_id"))
    try:
        result = await parser.parse_live(room_id)
    except Exception as e:
        pytest.skip(f"B站直播解析失败: {e} (风控)")

    logger.debug(f"result: {result}")
    assert result.title, "标题为空"
    assert result.author, "作者为空"

    avatar_path = await result.author.get_avatar_path()
    assert avatar_path, "头像不存在"
    assert avatar_path.exists(), "头像不存在"

    img_contents = result.img_contents
    for img_content in img_contents:
        path = await img_content.get_path()
        assert path.exists(), "图片不存在"

    logger.success("B站直播解析成功")


@pytest.mark.xfail(reason="老版专栏已废弃")
async def test_read():
    logger.info("开始解析B站图文 https://www.bilibili.com/read/cv523868")
    from nonebot_plugin_parser.parsers import BilibiliParser

    url = "https://www.bilibili.com/read/cv523868"
    parser = BilibiliParser()
    _, searched = parser.search_url(url)
    result = await parser._parse_read(searched)
    logger.debug(f"result: {result}")
    assert result.title, "标题为空"
    assert result.author, "作者为空"
    avatar_path = await result.author.get_avatar_path()
    assert avatar_path, "头像不存在"
    assert avatar_path.exists(), "头像不存在"

    assert result.graphics, "graphics 为空"
    await result.ensure_downloads_complete()

    logger.success("B站图文解析成功")


@pytest.mark.asyncio
async def test_dynamic():
    from nonebot_plugin_parser.parsers import BilibiliParser

    dynamic_urls = [
        "https://t.bilibili.com/1120105154190770281",
        "https://www.bilibili.com/opus/998440765151510535",
        "https://www.bilibili.com/opus/1040093151889457152",
    ]

    parser = BilibiliParser()

    async def test_parse_dynamic(dynamic_url: str) -> None:
        _, searched = parser.search_url(dynamic_url)
        # 根据 URL 类型选择解析方法
        # /opus/ URL 也使用 dynamic_id 组
        if searched.group("dynamic_id"):
            dynamic_id = int(searched.group("dynamic_id"))
            result = await parser.parse_dynamic_or_opus(dynamic_id)
        else:
            raise ValueError(f"无法解析 URL: {dynamic_url}")
        assert result.author, "作者为空"
        avatar_path = await result.author.get_avatar_path()
        assert avatar_path, "头像不存在"
        assert avatar_path.exists(), "头像不存在"

        await result.ensure_downloads_complete()

    await asyncio.gather(*[test_parse_dynamic(dynamic_url) for dynamic_url in dynamic_urls])
    logger.success("B站动态解析成功")


def test_fallback_select_streams_filters_none_codecs():
    from bilibili_api.video import VideoQuality, VideoCodecs, AudioQuality
    from bilibili_api.video import VideoStreamDownloadURL, AudioStreamDownloadURL
    from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

    v_good = VideoStreamDownloadURL(url="https://example.com/v1", video_quality=VideoQuality._480P, video_codecs=VideoCodecs.AVC)
    v_none_codecs = VideoStreamDownloadURL(url="https://example.com/v2", video_quality=VideoQuality._480P, video_codecs=None)
    v_low = VideoStreamDownloadURL(url="https://example.com/v3", video_quality=VideoQuality._360P, video_codecs=VideoCodecs.AV1)
    a_high = AudioStreamDownloadURL(url="https://example.com/a1", audio_quality=AudioQuality._192K)
    a_low = AudioStreamDownloadURL(url="https://example.com/a2", audio_quality=AudioQuality._64K)

    # 混合 codecs=None 的流，应过滤并选最佳
    result = BilibiliParser._fallback_select_streams([v_none_codecs, v_good, v_low, a_high, a_low])
    assert result[0] is v_good
    assert result[1] is a_high

    # 全部 codecs=None → 返回 None
    result = BilibiliParser._fallback_select_streams([v_none_codecs, a_high])
    assert result[0] is None
    assert result[1] is a_high

    # 质量上限过滤
    result = BilibiliParser._fallback_select_streams([v_good, v_low], max_quality=VideoQuality._360P.value)
    assert result[0] is v_low

    # 空列表
    result = BilibiliParser._fallback_select_streams([])
    assert result == [None, None]
