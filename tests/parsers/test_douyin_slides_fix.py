"""Verification test: live-photo slides URL parses to 2 videos.

This isolates the parsing fix (PC web detail API) from the flaky download layer.
回归: 修复前 parse_slides 用 slidesinfo v2 API, images 无 video 字段,
导致只输出 ImageContent; 修复后用 PC web detail API, 正确取到视频 URL。
"""

import pytest
from nonebot import logger


@pytest.mark.asyncio
async def test_live_photo_slides_parses_to_videos():
    """实况照片(live photo)图集必须解析出 DynamicContent(视频) 而非静态图片。"""
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    # 用户提供的链接: 2 张实况照片(live photo), 每张对应一段 mp4
    url = "https://v.douyin.com/Gz4nn_2caaU"

    keyword, searched = parser.search_url(url)
    assert searched, "无法匹配 URL"
    result = await parser.parse(keyword, searched)

    content_types = [type(c).__name__ for c in result.contents]
    logger.info(
        f"title={result.title!r}, contents types={content_types}, "
        f"dynamic_contents={len(result.dynamic_contents)}, "
        f"img_contents={len(result.img_contents)}"
    )

    # 核心断言: 必须解析出 2 段实况照片视频
    assert len(result.dynamic_contents) == 2, f"实况照片应解析出 2 段视频, 实际 contents={content_types}"
