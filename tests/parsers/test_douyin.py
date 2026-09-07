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


@pytest.mark.asyncio
async def test_common_video():
    """测试普通视频"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    parser = DouyinParser()

    common_urls = [
        "https://v.douyin.com/_2ljF4AmKL8/",
        "https://www.douyin.com/video/7521023890996514083",
    ]

    async def test_parse(url: str) -> None:
        logger.info(f"{url} | 开始解析抖音视频")
        keyword, searched = parser.search_url(url)
        assert searched, "无法匹配 URL"

        result = await parser.parse(keyword, searched)
        logger.debug(f"{url} | 解析结果: \n{result}")

        assert result.title, "标题为空"
        assert result.author, "作者为空"
        assert await result.cover_path(), "封面为空"
        assert result.video_contents, "视频内容为空"

        video_path = await result.video_contents[0].get_path()

        assert video_path.exists(), "视频不存在"
        logger.success(f"{url} | 抖音视频解析成功")

    for url in common_urls:
        try:
            await test_parse(url)
        except DownloadException:
            pytest.skip("抖音视频下载失败, 随机到的 cdn 过期")


@pytest.mark.asyncio
async def test_old_video():
    """老视频，网页打开会重定向到 m.ixigua.com"""

    # from nonebot_plugin_parser.parsers.douyin import DouYin

    # parser = DouYin()
    # # 该作品已删除，暂时忽略
    # url = "https://v.douyin.com/iUrHrruH"
    # logger.info(f"开始解析抖音西瓜视频 {url}")
    # video_info = await parser.parse_share_url(url)
    # logger.debug(f"title: {video_info.title}")
    # assert video_info.title
    # logger.debug(f"author: {video_info.author}")
    # assert video_info.author
    # logger.debug(f"cover_url: {video_info.cover_url}")
    # assert video_info.cover_url
    # logger.debug(f"video_url: {video_info.video_url}")
    # assert video_info.video_url
    # logger.success(f"抖音西瓜视频解析成功 {url}")


@pytest.mark.asyncio
async def test_note():
    """测试普通图文"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    parser = DouyinParser()

    note_urls = [
        "https://www.douyin.com/note/7469411074119322899",
        "https://v.douyin.com/iP6Uu1Kh",
    ]

    async def test_parse(url: str) -> None:
        logger.info(f"{url} | 开始解析抖音图文")
        keyword, searched = parser.search_url(url)
        assert searched, "无法匹配 URL"

        result = await parser.parse(keyword, searched)
        logger.debug(f"{url} | 解析结果: \n{result}")
        assert result.title, "标题为空"
        assert result.author, "作者为空"
        if img_contents := result.img_contents:
            for img_content in img_contents:
                path = await img_content.get_path()
                assert path.exists(), "图片不存在"
        logger.success(f"{url} | 抖音图文解析成功")

    for url in note_urls:
        try:
            await test_parse(url)
        except DownloadException:
            pytest.skip("抖音 note 下载失败")


@pytest.mark.asyncio
async def test_detail_open_api_primary_and_fallback_order(monkeypatch):
    """open-api 是主力形态; 被风控时依次退回 签名 -> Bytespider。

    2026-09-07 起请求链为 open-api -> 签名(a_bogus, 有凭据附 Cookie) ->
    Bytespider; 旧 m/iesdouyin 分享页 parse_video 兜底已删除(改版后 0 成功)。
    """
    import json as _json

    import httpx

    from nonebot_plugin_parser.parsers import DouyinParser

    detail_json = _json.dumps(
        {
            "aweme_detail": {
                "author": {
                    "nickname": "fallback-tester",
                    "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
                },
                "desc": "open-api primary regression",
                "create_time": 1757200000,
                "images": None,
                "video": {
                    "play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=vtest"]},
                    "cover": {"url_list": ["https://example.com/cover.jpg"]},
                    "duration": 15000,
                },
            }
        }
    ).encode()

    calls: list[str] = []
    detail_url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

    class _Resp:
        def __init__(self, content: bytes, status_code: int = 200):
            self.status_code = status_code
            self.content = content
            self.request = httpx.Request("GET", detail_url)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"client error {self.status_code}",
                    request=self.request,
                    response=httpx.Response(self.status_code, request=self.request),
                )

    def _classify(headers: dict, params: dict) -> str:
        if "a_bogus" in params:
            return "signed"
        if headers.get("Origin") == "https://open.douyin.com":
            return "open-api"
        if "Bytespider" in (headers.get("User-Agent") or ""):
            return "bytespider"
        return "unknown"

    async def fake_request(_self, url, *, headers=None, params=None, **_kwargs):
        form = _classify(dict(headers or {}), dict(params or {}))
        calls.append(form)
        if form == "bytespider":
            # open-api 与签名被 403, Bytespider 返回完整数据
            return _Resp(detail_json)
        _Resp(b"", 403).raise_for_status()

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    keyword, searched = parser.search_url("https://www.douyin.com/video/7681253720335650091")
    assert searched, "无法匹配 URL"

    result = await parser.parse(keyword, searched)
    assert result.title == "open-api primary regression", "退回链未解析出结果"
    assert result.author.name == "fallback-tester"

    # 依次退回: open-api(主力) -> 签名 -> Bytespider
    assert calls == ["open-api", "signed", "bytespider"], f"退回顺序异常: {calls}"
    logger.success("open-api 主力 + 依次退回链路验证通过")


@pytest.mark.asyncio
async def test_slides():
    """
    含视频的图集(实况照片/live photo)
    https://v.douyin.com/Gz4nn_2caaU # 实况照片, 解析出 2 段视频
    https://www.douyin.com/note/7450744229229235491 # 解析成 4 段实况照片视频

    slides 类型无可用兜底 (m/iesdouyin 分享页均无 _ROUTER_DATA),
    note/slides 类型只有 detail API 一条路(分享页兜底已删除), 因此整个 test_slides
    都依赖 PC web detail 接口能拿到完整数据, 必须配置 parser_douyin_ttwid。
    """
    _needs_douyin_ttwid()
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    parser = DouyinParser()

    live_photo_url = "https://v.douyin.com/Gz4nn_2caaU"

    logger.info(f"开始解析抖音图集(实况照片解析出视频) {live_photo_url}")
    keyword, searched = parser.search_url(live_photo_url)
    assert searched, "无法匹配 URL"
    result = await parser.parse(keyword, searched)
    logger.debug(f"{live_photo_url} | 解析结果: \n{result}")
    assert result.title, "标题为空"

    # 关键断言: 实况照片必须解析出 DynamicContent(视频), 而非静态图片
    dynamic_contents = result.dynamic_contents
    assert len(dynamic_contents) == 2, (
        f"实况照片应解析出 2 段视频, 实际得到 {len(dynamic_contents)} 段 "
        f"(contents={[type(c).__name__ for c in result.contents]})"
    )
    for dynamic_content in dynamic_contents:
        try:
            path = await dynamic_content.get_path()
        except DownloadException:
            pytest.skip("抖音动态内容下载失败, 随机到的 cdn 过期")
        assert path.exists(), "动态内容不存在"
    logger.success(f"抖音图集(实况照片解析出视频)解析成功 {live_photo_url}")

    static_image_url = "https://www.douyin.com/note/7450744229229235491"
    logger.info(f"开始解析抖音图集(含视频解析出静态图片) {static_image_url}")
    keyword, searched = parser.search_url(static_image_url)
    assert searched, "无法匹配 URL"
    result = await parser.parse(keyword, searched)
    logger.debug(f"{static_image_url} | 解析结果: \n{result}")
    assert result.title, "标题为空"
    # 该 note 实为 4 段实况照片(live photo), note 改走 parse_slides 后正确输出视频
    dynamic_contents = result.dynamic_contents
    assert len(dynamic_contents) == 4, (
        f"该实况照片 note 应解析出 4 段视频, 实际 {len(dynamic_contents)} "
        f"(contents={[type(c).__name__ for c in result.contents]})"
    )
    for dynamic_content in dynamic_contents:
        try:
            path = await dynamic_content.get_path()
        except DownloadException:
            pytest.skip("抖音动态内容下载失败, 随机到的 cdn 过期")
        assert path.exists(), "动态内容不存在"
    logger.success(f"抖音图集(实况照片 note 解析出视频)解析成功 {static_image_url}")
