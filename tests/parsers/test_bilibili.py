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
    from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

    # 构造一份含 hvc1 流(上游 issue #1035 失败场景)的 dash 数据
    dash_data = {
        "dash": {
            "video": [
                {"id": VideoQuality._480P.value, "baseUrl": "https://example.com/v1",
                 "codecs": "avc1.64001f"},
                # hvc1: 上游 VideoCodecs.HEV.value="hev" 无法匹配 → 原 detect() 置 None
                {"id": VideoQuality._1080P.value, "baseUrl": "https://example.com/v2",
                 "codecs": "hvc1.1.6.L180.90"},
                {"id": VideoQuality._360P.value, "baseUrl": "https://example.com/v3",
                 "codecs": "av01.0.08M.08"},
            ],
            "audio": [
                {"id": AudioQuality._192K.value, "baseUrl": "https://example.com/a1"},
                {"id": AudioQuality._64K.value, "baseUrl": "https://example.com/a2"},
            ],
        }
    }

    # 默认全部编码允许 → 应恢复 hvc1 流并选最高清晰度 1080P
    result = BilibiliParser._fallback_select_streams(
        dash_data, max_quality=VideoQuality._8K
    )
    assert result[0].video_codecs is VideoCodecs.HEV
    assert result[0].video_quality is VideoQuality._1080P
    assert result[1].audio_quality is AudioQuality._192K

    # 限制编码白名单不含 HEV → hvc1 流被过滤，应选次高的 AVC 480P
    result = BilibiliParser._fallback_select_streams(
        dash_data, max_quality=VideoQuality._8K,
        allowed_codecs=[VideoCodecs.AVC, VideoCodecs.AV1],
    )
    assert result[0].video_codecs is VideoCodecs.AVC
    assert result[0].video_quality is VideoQuality._480P

    # 质量上限过滤(传枚举)
    result = BilibiliParser._fallback_select_streams(
        dash_data, max_quality=VideoQuality._360P
    )
    assert result[0].video_quality is VideoQuality._360P

    # 无法识别编码的流被丢弃
    bad_data = {"dash": {"video": [
        {"id": VideoQuality._480P.value, "baseUrl": "https://example.com/x",
         "codecs": "weird-codec-xyz"},
    ], "audio": []}}
    result = BilibiliParser._fallback_select_streams(bad_data)
    assert result[0] is None

    # 空数据
    assert BilibiliParser._fallback_select_streams({}) == [None, None]
