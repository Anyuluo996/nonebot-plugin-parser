"""Verification test: live-photo slides parses to 2 full-duration videos with covers.

回归: 修复前 parse_slides 用 slidesinfo v2 API, images 无 video 字段,
导致只输出 ImageContent; 修复后用 PC web detail API, 正确取到视频地址,
使用 download_addr (完整时长含原始音频) 并附带封面用于渲染缩略图。
"""

import json as _json
import asyncio
import subprocess

import httpx
import pytest
from nonebot import logger

VID = "7650785539410446179"
URL = "https://v.douyin.com/Gz4nn_2caaU"
PC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
}


@pytest.mark.asyncio
async def test_decoder_picks_download_addr_with_covers():
    """decoder 优先选择 download_addr (完整视频), 且每个视频都带封面。"""
    from nonebot_plugin_parser.parsers.douyin import slides

    async with httpx.AsyncClient(headers=PC_HEADERS, verify=False, timeout=30) as c:
        r = await c.get(
            "https://www.douyin.com/aweme/v1/web/aweme/detail/",
            params={"aweme_id": VID, "aid": "6383"},
        )
        aweme_detail = slides.detail_decoder.decode(r.content).aweme_detail

    assert aweme_detail is not None, "aweme_detail 为空"
    dynamic_urls = aweme_detail.dynamic_urls
    assert len(dynamic_urls) == 2, f"应解析出 2 段视频, 实际 {len(dynamic_urls)}"

    # 断言: dynamic_urls 是 download_addr 完整版, 且优先使用官方 play API 形式
    # (CDN 镜像 /mps/logo/ 偶发 403/404, 官方 play API 更稳定)
    for i, u in enumerate(dynamic_urls):
        is_full = "watermark=1" in u or "/mps/logo/" in u
        assert is_full, f"dynamic[{i}] 不是 download_addr 完整版 URL: {u[:120]}"
        is_play_api = "/aweme/v1/play" in u
        assert is_play_api, f"dynamic[{i}] 未优先使用官方 play API URL(易触发 403): {u[:120]}"

    # 断言: 每个视频都有封面
    cover_urls = aweme_detail.dynamic_cover_urls
    assert len(cover_urls) == 2, f"应解析出 2 个封面, 实际 {len(cover_urls)}"
    for i, u in enumerate(cover_urls):
        assert u, f"cover[{i}] 为空"
        assert u.startswith("http"), f"cover[{i}] 不是 http URL: {u}"


@pytest.mark.asyncio
async def test_live_photo_slides_parses_to_videos():
    """端到端: parse_slides 输出 2 段带封面的 DynamicContent 视频内容。"""
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    keyword, searched = parser.search_url(URL)
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

    # 断言: 每段视频都带封面(否则渲染图无法显示缩略图)
    for i, cont in enumerate(result.dynamic_contents):
        assert cont.cover is not None, f"dynamic_contents[{i}] 缺少封面 cover"

    # 可选断言: 下载成功时验证时长是完整版本(download_addr, >3s)
    for i, cont in enumerate(result.dynamic_contents):
        try:
            path = await cont.get_path()
        except Exception as e:
            logger.warning(f"dynamic[{i}] 下载失败(CDN 波动), 跳过时长断言: {e}")
            continue
        try:
            out = await asyncio.to_thread(
                subprocess.run,
                ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            duration = float(_json.loads(out.stdout)["format"]["duration"])
            logger.info(f"dynamic[{i}] duration={duration:.2f}s")
            # play_addr 预览约 2.1s, download_addr 完整版约 5.1s
            assert duration > 3.0, f"dynamic[{i}] 时长 {duration:.2f}s 偏短, 可能取到的是 play_addr 预览流"
        except (FileNotFoundError, KeyError, ValueError):
            logger.warning(f"dynamic[{i}] 无法用 ffprobe 检测时长, 跳过时长断言")
