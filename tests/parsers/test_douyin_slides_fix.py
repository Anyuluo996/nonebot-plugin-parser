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

import pytest
from nonebot import logger


def _needs_douyin_ttwid():
    """运行时判断是否配置了登录态 ttwid, 未配置则 skip 当前测试。

    抖音 PC web detail 接口要求登录态 ttwid + a_bogus 签名配套才放行,
    缺一即返回 200 + 空 body, 实况照片/动态视频无法解析。
    (a_bogus 签名由 parser 自动计算, ttwid 需用户配置。)

    必须运行时判断 (而非模块顶层 skipif): conftest 的 session 级 init fixture
    在 collect 之后才跑, 模块顶层 import pconfig 会因 NoneBot 未初始化而拿到
    False, 导致即使配了 ttwid 也误 skip (issue: DOuyin_Note_Slides_Decode_Failure)。
    """
    try:
        from nonebot_plugin_parser.config import pconfig

        if pconfig.douyin_ttwid:
            return
    except Exception:
        pass
    pytest.skip(
        "未配置 parser_douyin_ttwid, 抖音 PC web detail 接口要求登录态 ttwid + a_bogus "
        "签名配套, 缺 ttwid 返回空 body, 实况照片/dynamic 视频无法解析"
    )


# 实况照片 slides (share_type=slides, 含 live photo), 走 parser.parse_slides 路径
URL = "https://v.douyin.com/Gz4nn_2caaU"
# 重定向成 note/ 的实况照片图文 (share_type=note, 含 live photo)
LIVE_NOTE_URL = "https://v.douyin.com/PsRRzmKjer8/"
# 对应的 note id (test_note_empty_body_falls_back_to_parse_video 用)
LIVE_NOTE_VID = "7651838242916592867"


@pytest.mark.asyncio
async def test_decoder_picks_play_addr_with_covers():
    """decoder 使用 play_addr (无水印/无片尾), 且每个视频都带封面。

    走 parser.parse_slides 完整路径 (含 a_bogus 签名), 从结果反推 decoder 选取正确。
    直接裸 httpx 打 detail 接口缺 a_bogus 签名会返回空 body, 故必须走 parser。
    """
    _needs_douyin_ttwid()
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    keyword, searched = parser.search_url(URL)
    assert searched, "无法匹配 URL"
    result = await parser.parse(keyword, searched)

    # SlidesData 格式下应全部解析为 dynamic (实况视频), 无静态图
    dynamics = result.dynamic_contents
    assert len(dynamics) == 2, f"应解析出 2 段视频, 实际 {len(dynamics)}"

    # 断言: 每个实况视频都有封面 (decoder 选取了 play_addr 对应的 cover)
    for i, cont in enumerate(dynamics):
        assert cont.cover is not None, f"dynamic_contents[{i}] 缺少封面 cover"


@pytest.mark.asyncio
async def test_live_photo_slides_parses_to_videos():
    """端到端: parse_slides 输出 2 段带封面的 DynamicContent 视频内容。

    注意: slides 类型无可用兜底 (m/iesdouyin 分享页均无 _ROUTER_DATA),
    在 PC detail 风控下 slides 链接直接 ParseException, 与 note 行为不同。
    """
    _needs_douyin_ttwid()
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


@pytest.mark.asyncio
async def test_live_photo_note_redirect_parses_to_video():
    """回归2: 重定向成 note/ 的实况照片图文必须解析出 DynamicContent 视频。

    修复前 note 走 parse_video, _ROUTER_DATA 的 images 不含 video 字段,
    只输出 1 张静态图, 实况视频丢失; 修复后 note 优先走 parse_slides,
    PC detail API 返回 images[].video.play_addr, 正确输出实况视频。
    """
    _needs_douyin_ttwid()
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


@pytest.mark.asyncio
async def test_decoder_picks_live_video_for_note():
    """端到端: note 实况照片 (重定向成 note/) 解析出实况视频。

    走 parser 完整路径 (含 a_bogus 签名); 裸 httpx 缺签名会空 body。
    """
    _needs_douyin_ttwid()
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    keyword, searched = parser.search_url(LIVE_NOTE_URL)
    assert searched, "无法匹配 URL"
    result = await parser.parse(keyword, searched)

    dynamics = result.dynamic_contents
    assert dynamics, f"note 实况照片应解析出至少 1 段视频, 实际 {len(dynamics)}"


@pytest.mark.asyncio
async def test_note_empty_body_falls_back_to_parse_video(monkeypatch):
    """回归3 (issue: DOuyin_Note_Slides_Decode_Failure): PC detail 接口返回
    200 + 空 body 时, note 必须降级到 parse_video 而非 traceback。

    修复前: msgspec.DecodeError 未被 note 的 ``except ParseException`` 捕获,
    直接 traceback; 修复后: 空 body 主动转 ParseException, note fallback 生效。

    注: 抖音 2026-08 改版后 m/iesdouyin 分享页 _ROUTER_DATA 不再含 videoInfoRes,
    真实分享页无法提供数据; 本测试改为 mock 分享页返回有效 _ROUTER_DATA,
    专注验证 "detail 空 body → fallback parse_video" 的回退逻辑本身,
    不依赖外部网站可用性。
    """
    import json as _json

    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()

    # PC detail 接口返回空 body (模拟抖音风控)
    class _EmptyResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.content = b""
            self.text = ""
            self.headers = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    # 分享页兜底返回有效 _ROUTER_DATA (含 images, 模拟改版前的分享页结构)
    _router_data = _json.dumps(
        {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "item_list": [
                            {
                                "create_time": 1700000000,
                                "author": {
                                    "nickname": "fallback-author",
                                    "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
                                },
                                "desc": "fallback note (分享页 _ROUTER_DATA)",
                                "images": [{"url_list": ["https://example.com/img1.jpg"]}],
                            }
                        ]
                    }
                }
            }
        }
    )

    class _RouterDataResp:
        def __init__(self) -> None:
            self.status_code = 200
            self.text = f"<script>window._ROUTER_DATA = {_router_data}</script>"
            self.content = self.text.encode("utf-8")
            self.headers = {"content-type": "text/html"}

        @property
        def url(self):
            return "https://www.iesdouyin.com/share/note/"

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            return _EmptyResp()
        if "share/note" in str(url) or "share/video" in str(url):
            return _RouterDataResp()
        raise RuntimeError(f"unexpected URL: {url}")

    # mock 下载层: parse_video 的 create_image_contents 会调度 download_img
    async def _coro(*args, **kwargs):
        return __import__("pathlib").Path("/fake/img")

    def _stub_dl(*args, **kwargs):
        return asyncio.create_task(_coro(*args, **kwargs))

    monkeypatch.setattr(parser, "request", _fake_request)
    monkeypatch.setattr(parser.downloader, "download_img", _stub_dl)

    # note 类型: PC detail 失败应 fallback 到 parse_video (mock 分享页)
    keyword, searched = parser.search_url(f"https://www.iesdouyin.com/share/note/{LIVE_NOTE_VID}")
    assert searched, "note URL 未匹配"
    result = await parser.parse(keyword, searched)

    # fallback 到 parse_video 后应返回标题 + 至少一些内容 (images 静态图)
    assert result.title == "fallback note (分享页 _ROUTER_DATA)"
    assert result.contents, "fallback 后应至少返回静态图内容"


# === 回归4: isPicture=true 的 picture 类型图文 ===
# 旧 Struct 假设 author/images[] 结构, 跟 pictureList[] 完全对不上, 直接 ValidationError。
# 修复: 新增 PictureSlidesData + decode_aweme_detail 智能 dispatch。
# 该测试不需要 ttwid, 用 monkeypatch 喂 mock 响应即可, 应当全平台都能跑通。
PICTURE_NOTE_VID = "7450744229229235491"

# 真实 PC detail 响应 (issue DOuyin_Note_Slides_Decode_Failure 提供)
_PICTURE_NOTE_PAYLOAD = {
    "aweme_detail": {
        "awemeId": PICTURE_NOTE_VID,
        "nickname": "平平淡淡-",
        "createTime": 1734761606000,  # 毫秒
        "uid": "61147416465",
        "desc": "小米塔可爱捏\n#米塔 #steam游戏",
        "isPicture": True,
        "music": {
            "play_addr": {
                "url_list": [
                    "https://www.douyin.com/aweme/v1/play/?music_id=tgm_bgm_001",
                ]
            }
        },
        "pictureList": [
            {
                "width": 540,
                "height": 542,
                "url": "https://p3-pc-sign.douyinpic.com/img1",
                "videoBitRateList": [
                    {
                        "cover": "https://p3-pc-sign.douyinpic.com/cov1",
                        "bitRate": 637347,
                        "dataSize": 488288,
                        "format": "mp4",
                        "isH265": 0,
                        "fps": 30,
                        "gearName": "normal_540_0",
                        "qualityType": 20,
                        "width": 540,
                        "height": 542,
                        "url": "https://www.douyin.com/aweme/v1/play/?file_id=f1",
                        "backUrl": [],
                    }
                ],
            },
            {
                "width": 1008,
                "height": 660,
                "url": "https://p3-pc-sign.douyinpic.com/img2",
                "videoBitRateList": [
                    {
                        "cover": "https://p3-pc-sign.douyinpic.com/cov2",
                        "bitRate": 1013317,
                        "dataSize": 405707,
                        "format": "mp4",
                        "isH265": 0,
                        "fps": 30,
                        "gearName": "normal_540_0",
                        "qualityType": 20,
                        "width": 880,
                        "height": 576,
                        "url": "https://www.douyin.com/aweme/v1/play/?file_id=f2",
                        "backUrl": [],
                    }
                ],
            },
            {
                "width": 2560,
                "height": 1600,
                "url": "https://p3-pc-sign.douyinpic.com/img3",
                "videoBitRateList": [
                    {
                        "cover": "https://p3-pc-sign.douyinpic.com/cov3",
                        "bitRate": 641916,
                        "dataSize": 2118324,
                        "format": "mp4",
                        "isH265": 0,
                        "fps": 30,
                        "gearName": "normal_720_0",
                        "qualityType": 10,
                        "width": 1152,
                        "height": 720,
                        "url": "https://www.douyin.com/aweme/v1/play/?file_id=f3",
                        "backUrl": [],
                    }
                ],
            },
            {
                "width": 2560,
                "height": 1600,
                "url": "https://p3-pc-sign.douyinpic.com/img4",
                "videoBitRateList": [
                    {
                        "cover": "https://p9-pc-sign.douyinpic.com/cov4",
                        "bitRate": 1347295,
                        "dataSize": 1235133,
                        "format": "mp4",
                        "isH265": 0,
                        "fps": 30,
                        "gearName": "normal_720_0",
                        "qualityType": 10,
                        "width": 1152,
                        "height": 720,
                        "url": "https://www.douyin.com/aweme/v1/play/?file_id=f4",
                        "backUrl": [],
                    }
                ],
            },
        ],
    }
}


@pytest.mark.asyncio
async def test_picture_note_decodes_picture_list(monkeypatch):
    """回归4: isPicture=true 的 note 必须解析 pictureList[], 输出 4 段 dynamic。

    修复前: 旧 Struct 假设 author/images[], 跟 pictureList[] 字段不匹配, decode 抛
    ValidationError → traceback; 修复后: PictureSlidesData 适配 pictureList[],
    decode_aweme_detail 智能 dispatch 自动选对结构, 4 段 live photo 全部解析。
    """
    import json as _json
    from typing import ClassVar

    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    raw = _json.dumps(_PICTURE_NOTE_PAYLOAD).encode("utf-8")

    class _MockResp:
        status_code = 200
        content = raw
        text = raw.decode("utf-8")
        # ClassVar 标注避免 ruff RUF012 mutable default 误报
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            return _MockResp()
        # 其它请求(分享页兜底)走真实网络 — 但 parse_slides 不会调, 故抛错
        raise RuntimeError(f"unexpected URL: {url}")

    # mock 下载层: 本测试只验证 decode 结构, 不实际下载视频/音频。
    # 因 music.play_url 存在会触发 download_audio + _merge_bgm 调度,
    # 不 mock 会产生真实网络请求 + ffmpeg 调用。
    async def _coro(*args, **kwargs):
        return __import__("pathlib").Path("/fake/media")

    def _stub_dl(*args, **kwargs):
        return asyncio.create_task(_coro(*args, **kwargs))

    async def _noop_merge(video_task, audio_task):
        await video_task
        await audio_task
        return __import__("pathlib").Path("/fake/merged.mp4")

    monkeypatch.setattr(parser, "request", _fake_request)
    monkeypatch.setattr(parser.downloader, "download_video", _stub_dl)
    monkeypatch.setattr(parser.downloader, "download_audio", _stub_dl)
    monkeypatch.setattr(parser.downloader, "download_img", _stub_dl)
    monkeypatch.setattr(parser, "_merge_bgm", _noop_merge)

    result = await parser.parse_slides(PICTURE_NOTE_VID)

    # 核心断言: 4 张图全是 live photo, 应输出 4 段 dynamic + 0 张静态图
    assert result.img_contents == [], f"全是 live photo, 静态图应为 0, 实际 {len(result.img_contents)}"
    assert len(result.dynamic_contents) == 4, f"应有 4 段 live photo 视频, 实际 {len(result.dynamic_contents)}"

    # 断言: 每段 dynamic 都带封面 (live video 的独立 cover, 来自 videoBitRateList[0].cover)
    for i, cont in enumerate(result.dynamic_contents):
        assert cont.cover is not None, f"dynamic_contents[{i}] 缺少封面 cover"

    # 断言: createTime 毫秒 -> 秒 转换正确 (datetime.fromtimestamp 期望秒)
    assert result.timestamp == 1734761606, f"createTime 毫秒没转秒: {result.timestamp}"

    # 断言: 标题/作者解出
    assert result.title == "小米塔可爱捏\n#米塔 #steam游戏"
    assert result.author is not None
    assert result.author.name == "平平淡淡-"


@pytest.mark.asyncio
async def test_picture_note_decodes_bgm_url(monkeypatch):
    """回归: music.play_url 必须被 decode, bgm_url 返回非 None。

    实况照片视频轨静音, BGM 在 aweme_detail.music.play_url;
    修复前 slides.py 未解析 music 字段, bgm_url 恒为 None, 合并逻辑无法触发。
    """
    import json as _json

    from nonebot_plugin_parser.parsers.douyin import slides

    raw = _json.dumps(_PICTURE_NOTE_PAYLOAD).encode("utf-8")

    aweme_detail = slides.decode_aweme_detail(raw)
    assert aweme_detail is not None, "decode 失败"
    assert aweme_detail.bgm_url is not None, "music 字段未解析, bgm_url 应非 None"
    assert "music_id=tgm_bgm_001" in aweme_detail.bgm_url


@pytest.mark.asyncio
async def test_picture_note_live_url_falls_back(monkeypatch):
    """回归4b: 真实 URL note/7450744229229235491 在 PC detail 风控时
    至少应返回 fallback 的静态图(同 test_note_empty_body_falls_back)。

    该测试不依赖 ttwid, 复现生产场景。
    Bytespider 兜底加入后, 无 ttwid 时签名请求空 body 会换爬虫 UA 重试,
    通常直接解出 4 段 dynamic; 爬虫通道也被风控时才降级 fallback 静态图。
    """
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    # 不 mock PC detail, 让真实空 body 触发 Bytespider 兜底链路
    # (若 ttwid 配置有效, 走 parse_slides 签名成功路径, 4 段 dynamic)
    kw, m = parser.search_url(f"https://www.douyin.com/note/{PICTURE_NOTE_VID}")
    assert m
    result = await parser.parse(kw, m)

    # 至少应有标题和内容 (Bytespider 兜底生效: 4 段 dynamic; 全风控: fallback 静态图)
    assert result.title, "标题不应为空"
    assert result.contents, "应至少返回静态图或 dynamic 视频"


@pytest.mark.asyncio
async def test_create_dynamic_contents_merges_bgm(monkeypatch):
    """回归: 传 bgm_url 时, create_dynamic_contents 应下载 BGM 并调度 _merge_bgm。

    实况照片视频轨静音, BGM 需合并; 此测试验证接线正确:
    - bgm_url 触发 download_audio
    - _merge_bgm 被调度 (path_task 被替换为 merge task)
    - bgm_url=None 时不触发合并 (其它平台不受影响)
    """
    from pathlib import Path

    from nonebot_plugin_parser.parsers.base import BaseParser

    # 用最小桩继承 BaseParser (其 __init__ 需要 COMMON_HEADER 等常量)
    # _abstract_parser=True 跳过 __init_subclass__ 的全局注册, 避免 _StubParser
    # 污染 BaseParser._registry 导致后续 register_parser_matcher() 访问
    # 不存在的 platform 属性而 AttributeError (测试隔离: 桩不应被当真实平台注册)
    class _StubParser(BaseParser):
        _abstract_parser = True

    parser = _StubParser()

    # 桩: download_video / download_img / download_audio 返回已完成的假 Task。
    # 真实方法被 @auto_task 装饰 (同步调用返回 Task), mock 需对齐此行为。
    async def _coro_video(*args, **kwargs):
        return Path("/fake/video.mp4")

    async def _coro_audio(*args, **kwargs):
        return Path("/fake/bgm.mp3")

    def _stub_download_video(*args, **kwargs):
        return asyncio.create_task(_coro_video(*args, **kwargs))

    def _stub_download_audio(*args, **kwargs):
        return asyncio.create_task(_coro_audio(*args, **kwargs))

    def _stub_download_img(*args, **kwargs):
        return asyncio.create_task(_coro_video(*args, **kwargs))

    merge_called = []

    async def _fake_merge_bgm(video_task, audio_task):
        merge_called.append(True)
        await video_task  # 消费 task 避免未消费告警
        await audio_task
        return Path("/fake/merged.mp4")

    monkeypatch.setattr(parser.downloader, "download_video", _stub_download_video)
    monkeypatch.setattr(parser.downloader, "download_audio", _stub_download_audio)
    monkeypatch.setattr(parser.downloader, "download_img", _stub_download_img)
    monkeypatch.setattr(parser, "_merge_bgm", _fake_merge_bgm)

    # Case 1: 带 bgm_url → _merge_bgm 应被调度
    contents = parser.create_dynamic_contents(
        ["https://example.com/v1", "https://example.com/v2"],
        cover_urls=["https://example.com/c1", "https://example.com/c2"],
        bgm_url="https://example.com/bgm",
    )
    assert len(contents) == 2
    # 等待所有 task 完成, 让 _merge_bgm 协程执行
    await asyncio.gather(*[c.get_path() for c in contents])
    assert len(merge_called) == 2, f"bgm_url 存在时应调度 2 次 _merge_bgm, 实际 {len(merge_called)}"

    # Case 2: 不带 bgm_url (默认 None) → _merge_bgm 不应被调度
    merge_called.clear()
    contents2 = parser.create_dynamic_contents(
        ["https://example.com/v3"],
        cover_urls=["https://example.com/c3"],
    )
    await asyncio.gather(*[c.get_path() for c in contents2])
    assert len(merge_called) == 0, "bgm_url=None 时不应调度 _merge_bgm"


# === 回归5: 普通视频改由 PC detail API 解析 (2026-08 抖音改版) ===
# 改版后 m/iesdouyin 分享页 _ROUTER_DATA 不再含 videoInfoRes, parse_video 兜底
# 全线失效; 普通视频改走 parse_slides (detail API), 由 SlidesData 新增的 video
# 字段输出 VideoContent。
NORMAL_VIDEO_VID = "7672751899556311734"

# 真实 PC detail 响应结构 (容器内对 NORMAL_VIDEO_VID 实测):
#   - create_time 为 snake_case 秒 (旧格式, 非 camelCase createTime)
#   - images 为 null (纯视频: key 存在但值 None, 非 key 缺失)
#   - 顶层 video 含 play_addr/cover/duration
_NORMAL_VIDEO_PAYLOAD = {
    "aweme_detail": {
        "desc": "#福建话 #福建方言 #闽南语 #方言趣味分享 #福建人",
        "create_time": 1786451763,  # 秒 (旧格式 snake_case)
        "author": {
            "nickname": "吴影默",
            "avatar_thumb": {"url_list": ["https://p3.douyinpic.com/aweme/100x100/avatar.jpg"]},
        },
        "images": None,  # 纯视频: key 存在但值 null (实测, 非 key 缺失)
        "video": {
            "play_addr": {
                # playwm 水印直链, 验证 video_url 的 playwm→play 去水印
                "url_list": ["https://v11-weba.douyinvod.com/video/tos/playwm/normal.mp4"]
            },
            "cover": {"url_list": ["https://p3-pc-sign.douyinpic.com/image-cut/cover.jpg"]},
            "duration": 5620,  # 毫秒
        },
    }
}


@pytest.mark.asyncio
async def test_normal_video_decodes_play_addr(monkeypatch):
    """普通视频经 PC detail API 解析, SlidesData 承载 video.play_addr 输出 VideoContent。

    覆盖三个改版关键点:
    1. images=null 时 decode 不崩 (Optional 容忍, 非 key 缺失);
    2. video_url 正确提取并去水印 (playwm→play, 对齐 parse_video);
    3. parse_slides 的 video 分支输出 1 个带封面的 VideoContent。
    """
    import json as _json
    from typing import ClassVar

    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.parsers.douyin import slides

    parser = DouyinParser()
    raw = _json.dumps(_NORMAL_VIDEO_PAYLOAD).encode("utf-8")

    # 先验证 decoder 层: images=null 容忍, video_url 去水印, 字段提取正确
    aweme_detail = slides.decode_aweme_detail(raw)
    assert aweme_detail is not None, "decode 失败 (images=null 应被 Optional 容忍)"
    assert aweme_detail.video_url is not None, "普通视频应解出 video_url"
    assert "playwm" not in aweme_detail.video_url, "未去水印 (期望 playwm→play)"
    assert aweme_detail.cover_url is not None, "应解出 cover_url"
    assert aweme_detail.duration_ms == 5620, f"duration_ms 应为 5620, 实际 {aweme_detail.duration_ms}"

    class _MockResp:
        status_code = 200
        content = raw
        text = raw.decode("utf-8")
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            return _MockResp()
        raise RuntimeError(f"unexpected URL: {url}")

    async def _coro(*args, **kwargs):
        return __import__("pathlib").Path("/fake/media")

    def _stub_dl(*args, **kwargs):
        return asyncio.create_task(_coro(*args, **kwargs))

    monkeypatch.setattr(parser, "request", _fake_request)
    monkeypatch.setattr(parser.downloader, "download_video", _stub_dl)
    monkeypatch.setattr(parser.downloader, "download_img", _stub_dl)

    result = await parser.parse_slides(NORMAL_VIDEO_VID)

    # 纯视频: 1 个 VideoContent, 无实况动态/图片
    assert len(result.video_contents) == 1, (
        f"普通视频应输出 1 个 VideoContent, 实际 contents={[type(c).__name__ for c in result.contents]}"
    )
    assert result.dynamic_contents == [], "普通视频不应有实况动态"
    assert result.img_contents == [], "普通视频不应有图片"
    vc = result.video_contents[0]
    assert vc.cover is not None, "VideoContent 应带封面 (来自 video.cover)"
    assert vc.duration == 5620, f"时长应为 5620ms, 实际 {vc.duration}"
    assert result.title == "#福建话 #福建方言 #闽南语 #方言趣味分享 #福建人"
    assert result.author is not None
    assert result.author.name == "吴影默"


@pytest.mark.asyncio
async def test_normal_video_parse_prefers_detail_api(monkeypatch):
    """video 类型走 _parse_douyin 时优先 PC detail API, 不触发分享页兜底。

    改版后分享页 _ROUTER_DATA 无 videoInfoRes, parse_video 兜底必失败;
    _parse_douyin 的 video 分支应优先 parse_slides, 成功即返回。
    若误走兜底, _fake_request 对非 detail URL 会 raise 让测试失败。
    """
    import json as _json
    from typing import ClassVar

    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    raw = _json.dumps(_NORMAL_VIDEO_PAYLOAD).encode("utf-8")

    class _MockResp:
        status_code = 200
        content = raw
        text = raw.decode("utf-8")
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            return _MockResp()
        # 分享页兜底 (m.douyin / iesdouyin) 不应被调用
        raise RuntimeError(f"不应请求分享页兜底 URL (应优先 detail API): {url}")

    async def _coro(*args, **kwargs):
        return __import__("pathlib").Path("/fake/media")

    def _stub_dl(*args, **kwargs):
        return asyncio.create_task(_coro(*args, **kwargs))

    monkeypatch.setattr(parser, "request", _fake_request)
    monkeypatch.setattr(parser.downloader, "download_video", _stub_dl)
    monkeypatch.setattr(parser.downloader, "download_img", _stub_dl)

    keyword, searched = parser.search_url(f"https://www.douyin.com/video/{NORMAL_VIDEO_VID}")
    assert searched, "无法匹配 video URL"
    result = await parser.parse(keyword, searched)

    assert len(result.video_contents) == 1, "应经 detail API 解析出 1 个视频"
    assert result.video_contents[0].cover is not None
    assert result.title
    assert "福建话" in result.title


@pytest.mark.asyncio
async def test_douyin_parser_download_headers_have_referer():
    """抖音 douyinvod 视频直链需 Referer 防盗链, DouyinParser 下载头必须带上。

    改版后 video 走 PC detail API, play_addr 直链 (douyinvod) 缺 Referer 会 403
    (实测容器内无 Referer 403, 加 Referer 200 video/mp4); DouyinParser.__init__
    给 self.headers 补 Referer, create_video_content 等下载透传该 header。
    """
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    assert parser.headers.get("Referer") == "https://www.douyin.com/", (
        "DouyinParser 下载头缺 Referer, douyinvod 直链会 403"
    )


@pytest.mark.asyncio
async def test_detail_api_http_error_converted_to_parse_exception(monkeypatch):
    """detail API 偶发 403 风控/超时 (httpx.HTTPError) 应转 ParseException 走 fallback,
    而非 HTTPStatusError 直穿成 ERROR + traceback。

    回归: parse_slides 的 request 默认 raise_for_status=True, 403 抛 HTTPStatusError;
    修复前未被 except (ParseException, ValueError) 捕获 → traceback; 修复后 parse_slides
    主动捕获 httpx.HTTPError 转 ParseException, 上层 _parse_douyin fallback 生效。
    """
    import httpx

    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import ParseException

    parser = DouyinParser()
    req = httpx.Request("GET", "https://www.douyin.com/aweme/v1/web/aweme/detail/")
    resp = httpx.Response(403, request=req)

    async def _fake_request(*args, **kwargs):
        raise httpx.HTTPStatusError("403 Forbidden", request=req, response=resp)

    monkeypatch.setattr(parser, "request", _fake_request)

    with pytest.raises(ParseException, match="detail API request failed"):
        await parser.parse_slides(NORMAL_VIDEO_VID)


@pytest.mark.asyncio
async def test_detail_empty_body_retries_with_bytespider(monkeypatch):
    """回归: 签名请求被风控返回空 body 时, 换 Bytespider UA 免签名重打 detail API。

    实测 (2026-08-23): 抖音 detail 接口对自家 Bytespider 爬虫免 a_bogus 签名与
    登录态校验, 同机同 IP 仅换 UA 即返回完整 JSON; 浏览器 UA 则 200 + 空 body。
    修复前: 空 body 直接 ParseException, note/video 只能走注定失败的分享页兜底
    (slides 更是无路可退); 修复后: Bytespider 兜底返回与签名路径同构的数据,
    解析正常完成, 不依赖用户配置 ttwid。
    """
    import json as _json
    from typing import ClassVar

    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    raw = _json.dumps(_NORMAL_VIDEO_PAYLOAD).encode("utf-8")

    class _EmptyResp:
        status_code = 200
        content = b""
        text = ""
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    class _MockResp:
        status_code = 200
        content = raw
        text = raw.decode("utf-8")
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @property
        def url(self):
            return "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    detail_calls: list[dict] = []

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            detail_calls.append({"headers": kwargs.get("headers"), "params": kwargs.get("params")})
            # 第一次: 签名请求 (带 a_bogus) 被风控返回空 body; 之后: Bytespider 兜底返回完整数据
            return _EmptyResp() if len(detail_calls) == 1 else _MockResp()
        raise RuntimeError(f"unexpected URL: {url}")

    # mock 下载层: 只验证兜底解析链路, 不实际下载视频/封面
    async def _coro(*args, **kwargs):
        return __import__("pathlib").Path("/fake/media")

    def _stub_dl(*args, **kwargs):
        return asyncio.create_task(_coro(*args, **kwargs))

    monkeypatch.setattr(parser, "request", _fake_request)
    monkeypatch.setattr(parser.downloader, "download_video", _stub_dl)
    monkeypatch.setattr(parser.downloader, "download_img", _stub_dl)

    result = await parser.parse_slides(NORMAL_VIDEO_VID)

    # 兜底行为断言: 恰好两次 detail 请求, 首次签名, 二次 Bytespider UA 免签名
    assert len(detail_calls) == 2, f"应恰好两次 detail 请求 (签名 + Bytespider 兜底), 实际 {len(detail_calls)}"
    first_headers = detail_calls[0]["headers"] or {}
    first_params = detail_calls[0]["params"] or {}
    second_headers = detail_calls[1]["headers"] or {}
    second_params = detail_calls[1]["params"] or {}
    assert "a_bogus" in first_params, "首次请求应为带 a_bogus 的签名请求"
    assert "Bytespider" not in (first_headers.get("User-Agent") or ""), "首次请求不应已是爬虫 UA"
    assert "Bytespider" in (second_headers.get("User-Agent") or ""), "兜底请求应使用 Bytespider UA"
    assert "a_bogus" not in second_params, "Bytespider 兜底应免签名"

    # 解析结果断言: 兜底路径输出与签名路径同构 (1 个带封面视频)
    assert len(result.video_contents) == 1, (
        f"兜底应解出 1 个视频, 实际 contents={[type(c).__name__ for c in result.contents]}"
    )
    assert result.video_contents[0].cover is not None
    assert result.title
    assert "福建话" in result.title
