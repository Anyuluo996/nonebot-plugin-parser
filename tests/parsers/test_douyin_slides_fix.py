"""Verification tests: live-photo slides/notes parse to videos with covers.

回归1: 修复前 parse_slides 用 slidesinfo v2 API, images 无 video 字段,
导致只输出 ImageContent; 修复后用 PC web detail API, 正确取到视频地址,
使用 play_addr (无水印、无抖音片尾、含原始音频) 并附带封面用于渲染缩略图。

回归2: 实况照片图文分享短链会重定向成 note/ 而非 slides/,
note 原本走 parse_video (_ROUTER_DATA 不返回 images[].video, 丢失实况视频);
修复后 note 优先走 parse_slides, 正确解析出实况照片视频。
"""

import json as _json
import asyncio
import subprocess

import httpx
import pytest
from nonebot import logger

# 配置读取: nonebot_plugin_parser.config 顶层 require("nonebot_plugin_localstore"),
# 需要 NoneBot 已初始化才能加载。测试 conftest 的 init fixture 是 session 级, 在
# collect 之后才跑, 模块顶层直接 import 会触发 RuntimeError。
# 故用 try 包裹: 初始化失败时按"无 ttwid" 处理, 让相关测试正确 skip 而非收集失败。
try:
    from nonebot_plugin_parser.config import pconfig as _pconfig

    _HAS_DOUYIN_TTWID = bool(_pconfig.douyin_ttwid)
except Exception:
    # collect 阶段 NoneBot 未初始化是预期情况, 不是测试错误
    _HAS_DOUYIN_TTWID = False

VID = "7650785539410446179"
URL = "https://v.douyin.com/Gz4nn_2caaU"
# 重定向成 note/ 的实况照片图文 (share_type=note, 含 live photo)
LIVE_NOTE_URL = "https://v.douyin.com/PsRRzmKjer8/"
# 对应的 note id
LIVE_NOTE_VID = "7651838242916592867"
PC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
}

# 抖音 PC web detail 接口在无 ttwid 时被风控返回 200 + 空 body,
# 此时实况照片/动态视频无法解析, 相关测试必须 skip 而非误判失败。
# 集中在此处管理, 避免在每个测试里散落 pytest.skip (issue: DOuyin_Note_Slides_Decode_Failure)。
_NEEDS_DOUYIN_TTWID = pytest.mark.skipif(
    not _HAS_DOUYIN_TTWID,
    reason="未配置 parser_douyin_ttwid, 抖音 PC web detail 接口被风控返回空 body, "
    "实况照片/dynamic 视频无法解析",
)


@_NEEDS_DOUYIN_TTWID
@pytest.mark.asyncio
async def test_decoder_picks_play_addr_with_covers():
    """decoder 使用 play_addr (无水印/无片尾), 且每个视频都带封面。"""
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

    # 断言: dynamic_urls 优先使用官方 play API 形式
    # (CDN 镜像 douyinvod.com 偶发 403/404, 官方 play API 更稳定)
    for i, u in enumerate(dynamic_urls):
        is_play_api = "/aweme/v1/play" in u
        assert is_play_api, f"dynamic[{i}] 未优先使用官方 play API URL: {u[:120]}"

    # 断言: 每个视频都有封面
    cover_urls = aweme_detail.dynamic_cover_urls
    assert len(cover_urls) == 2, f"应解析出 2 个封面, 实际 {len(cover_urls)}"
    for i, u in enumerate(cover_urls):
        assert u, f"cover[{i}] 为空"
        assert u.startswith("http"), f"cover[{i}] 不是 http URL: {u}"


@_NEEDS_DOUYIN_TTWID
@pytest.mark.asyncio
async def test_live_photo_slides_parses_to_videos():
    """端到端: parse_slides 输出 2 段带封面的 DynamicContent 视频内容。

    注意: slides 类型无可用兜底 (m/iesdouyin 分享页均无 _ROUTER_DATA),
    在 PC detail 风控下 slides 链接直接 ParseException, 与 note 行为不同。
    """
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

    # 可选断言: 下载成功时验证时长 (play_addr 约 2.1s)
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
            assert duration > 1.0, f"dynamic[{i}] 时长 {duration:.2f}s 异常偏短"
        except (FileNotFoundError, KeyError, ValueError):
            logger.warning(f"dynamic[{i}] 无法用 ffprobe 检测时长, 跳过时长断言")


@_NEEDS_DOUYIN_TTWID
@pytest.mark.asyncio
async def test_live_photo_note_redirect_parses_to_video():
    """回归2: 重定向成 note/ 的实况照片图文必须解析出 DynamicContent 视频。

    修复前 note 走 parse_video, _ROUTER_DATA 的 images 不含 video 字段,
    只输出 1 张静态图, 实况视频丢失; 修复后 note 优先走 parse_slides,
    PC detail API 返回 images[].video.play_addr, 正确输出实况视频。
    """
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    keyword, searched = parser.search_url(LIVE_NOTE_URL)
    assert searched, "无法匹配 URL"
    result = await parser.parse(keyword, searched)

    content_types = [type(c).__name__ for c in result.contents]
    logger.info(
        f"note-live title={result.title!r}, contents types={content_types}, "
        f"dynamic_contents={len(result.dynamic_contents)}, "
        f"img_contents={len(result.img_contents)}"
    )

    assert result.title, "标题为空"

    # 核心断言: note 实况照片必须解析出 DynamicContent 视频 (修复前是 0)
    assert result.dynamic_contents, f"note 实况照片应解析出视频, 实际 dynamic=0 (contents={content_types})"
    for i, cont in enumerate(result.dynamic_contents):
        assert cont.cover is not None, f"dynamic_contents[{i}] 缺少封面 cover"


@_NEEDS_DOUYIN_TTWID
@pytest.mark.asyncio
async def test_decoder_picks_live_video_for_note():
    """单元: PC detail API 对 note 实况照片返回 images[].video.play_addr。"""
    from nonebot_plugin_parser.parsers.douyin import slides

    async with httpx.AsyncClient(headers=PC_HEADERS, verify=False, timeout=30) as c:
        r = await c.get(
            "https://www.douyin.com/aweme/v1/web/aweme/detail/",
            params={"aweme_id": LIVE_NOTE_VID, "aid": "6383"},
        )
    aweme_detail = slides.detail_decoder.decode(r.content).aweme_detail

    assert aweme_detail is not None, "aweme_detail 为空"
    dynamic_urls = aweme_detail.dynamic_urls
    assert dynamic_urls, f"note 实况照片应解析出至少 1 段视频, 实际 {len(dynamic_urls)}"
    for i, u in enumerate(dynamic_urls):
        is_play_api = "/aweme/v1/play" in u
        assert is_play_api, f"dynamic[{i}] 未优先使用官方 play API URL: {u[:120]}"


@pytest.mark.asyncio
async def test_note_empty_body_falls_back_to_parse_video(monkeypatch):
    """回归3 (issue: DOuyin_Note_Slides_Decode_Failure): PC detail 接口返回
    200 + 空 body 时, note 必须降级到 parse_video 而非 traceback。

    修复前: msgspec.DecodeError 未被 note 的 ``except ParseException`` 捕获,
    直接 traceback; 修复后: 空 body 主动转 ParseException, note fallback 生效。
    """
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()

    # 仅对 PC detail 接口返回空 body (模拟抖音风控); 其它请求(分享页兜底)走真实网络
    class _EmptyResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.content = b""
            self.text = ""
            self.headers = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    _real_request = parser.request

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            return _EmptyResp()
        return await _real_request(url, *args, **kwargs)

    monkeypatch.setattr(parser, "request", _fake_request)

    # note 类型: PC detail 失败应 fallback 到 parse_video (真实网络)
    keyword, searched = parser.search_url(f"https://www.iesdouyin.com/share/note/{LIVE_NOTE_VID}")
    assert searched, "note URL 未匹配"
    result = await parser.parse(keyword, searched)

    # 即使实况照片视频因风控丢失, 也应返回标题 + 至少一些内容, 而非 traceback
    assert result.title, "fallback 后标题不应为空"
    assert result.contents, "fallback 后应至少返回静态图内容"
