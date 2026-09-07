import re
import secrets
from typing import ClassVar
from urllib.parse import quote

from nonebot import logger

from ..base import (
    Platform,
    BaseParser,
    PlatformEnum,
    ParseException,
    handle,
)
from ._abogus import ABogus

# PC web 详情接口
_DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

# 签名形态用的新版 UA，避免 COMMON_HEADER 里 2016 年的 Chrome/55 UBrowser
# 直接被抖音风控识别为异常客户端。仅作用于签名请求，不改全局 COMMON_HEADER
# （后者被各 parser / 下载器复用，贸然升级可能影响其它平台）。
_PC_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 抖音自家 SEO 爬虫 UA。detail 接口对 Bytespider 免 a_bogus 签名与登录态校验
# (2026-08-23 实测: 同机同 IP 仅换 UA, 免签名请求即返回完整 aweme_detail JSON,
# 浏览器 UA 则 200 + 空 body)。最后保险: 字节若给爬虫加反查校验此路即失效。
_BYTE_SPIDER_UA = "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com; +https://zhanzhang.toutiao.com/)"

# ABogus 签名器实例。内部状态在 get_value 调用时会 reset，实例可安全复用，
# 避免每次请求都重建对象（SM3 表/浏览器信息等初始化开销）。
_ABOGUS = ABogus()

# 签名形态的通用请求参数（仿抖音 web 端 getCommonData）。
# 这些字段会被 a_bogus 签名纳入计算，缺失会导致签名失效被风控返回空 body。
# 取值用常量而非动态读取 navigator.*，服务端仅做存在性 + 格式校验。
_PC_WEB_COMMON_PARAMS: dict[str, str] = {
    "aid": "6383",
    "channel": "channel_pc_web",
    "device_platform": "webapp",
    "pc_client_type": "1",
    "pc_libra_divert": "Windows",
    "version_code": "170400",
    "version_name": "17.4.0",
    "cookie_enabled": "true",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Edge",
    "browser_version": "132.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "132.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "cpu_core_num": "16",
    "device_memory": "8",
    "platform": "PC",
    "effective_type": "4g",
    "round_trip_time": "100",
    "screen_width": "2195",
    "screen_height": "1235",
}


class DouyinParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.DOUYIN, display_name="抖音")

    def __init__(self):
        super().__init__()
        # 抖音 douyinvod 视频直链 (detail API 的 play_addr) 需 Referer 防盗链校验,
        # 缺失则 403; COMMON_HEADER 全局通用仅含 UA, 在此给本 parser 下载头补上。
        self.headers["Referer"] = "https://www.douyin.com/"

    # https://v.douyin.com/_2ljF4AmKL8
    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    # https://www.douyin.com/video/7521023890996514083
    # https://www.douyin.com/note/7469411074119322899
    @handle("douyin", r"douyin\.com/(?P<ty>video|note|slides)/(?P<vid>\d+)")
    @handle("iesdouyin", r"iesdouyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    @handle("m.douyin", r"m\.douyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    # https://jingxuan.douyin.com/m/video/7574300896016862490?app=yumme&utm_source=copy_link
    @handle("jingxuan.douyin", r"jingxuan\.douyin.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    async def _parse_douyin(self, searched: re.Match[str]):
        # 三种类型(video/note/slides)统一走 PC detail API:
        #   - slides/note: 只有 detail API 返回 images[].video(实况照片视频),
        #     分享页数据没有该字段;
        #   - video: 自抖音 2026-08 改版, 分享页 _ROUTER_DATA 不再含 videoInfoRes,
        #     旧 parse_video 兜底已死(2026-09-07 统计 7 天 0 成功), 直链只由
        #     detail API 提供。
        # 链接类型仅用于 URL 匹配, 解析行为不再区分。
        return await self.parse_slides(searched.group("vid"))

    async def _request_detail(self, video_id: str):
        """依次以三种形态请求 PC detail 接口: open-api -> 签名 -> Bytespider。

        - open-api (主力): 极简参数 {aweme_id, aid} + Origin/Referer 指向
          open.douyin.com, 该入口不校验 a_bogus 签名与登录态, 2026-09 实测最稳
          (上游 fllesser #584 同款形态);
        - 签名: 完整 PC web 参数 + a_bogus 签名, 配置了 ttwid/cookie 时附凭据,
          对无签名形态整体被封的场景兜底;
        - Bytespider (最后保险): 爬虫 UA 免签名, 字节加反查校验即失效。

        Returns:
            最后一次成功发出的响应(可能为空 body), 每种形态都请求异常时为 None。
            空 body / None 由调用方统一转 ParseException。
        """
        import httpx

        from . import ttwid as dy_ttwid

        variants: list[tuple[str, dict[str, str], dict[str, str]]] = []

        # 1. open-api 形态 (主力)
        variants.append(
            (
                "open-api",
                {
                    **self.headers,
                    "Origin": "https://open.douyin.com",
                    "Referer": "https://open.douyin.com/",
                },
                {"aweme_id": video_id, "aid": "6383"},
            )
        )

        # 2. 签名形态: PC web UA + X-Requested-With 缺一不可, 否则风控返回
        # 200 + 空 body; 组装完整参数后追加 msToken(仿浏览器随机占位) 与
        # a_bogus 签名(必须最后追加且 url 编码)。配置了 ttwid/cookie 时附上,
        # 抗风控能力显著更强 (凭据获取见 ttwid.get_effective_credential)。
        signed_headers = {
            **self.headers,
            "User-Agent": _PC_WEB_UA,
            "Referer": "https://www.douyin.com/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if credential := dy_ttwid.get_effective_credential():
            signed_headers["Cookie"] = credential
        signed_params: dict[str, str] = {
            **_PC_WEB_COMMON_PARAMS,
            "aweme_id": video_id,
            "msToken": secrets.token_hex(64),
        }
        signed_params["a_bogus"] = quote(_ABOGUS.get_value(signed_params), safe="")
        variants.append(("signed", signed_headers, signed_params))

        # 3. Bytespider 爬虫 UA (最后保险)
        variants.append(
            (
                "bytespider",
                {"User-Agent": _BYTE_SPIDER_UA},
                {
                    "aweme_id": video_id,
                    "aid": "6383",
                    "version_code": "170400",
                    "device_platform": "webapp",
                },
            )
        )

        response = None
        for name, form_headers, form_params in variants:
            try:
                response = await self.request(_DETAIL_URL, headers=form_headers, params=form_params)
            except httpx.HTTPError as e:
                # 403 风控/限流/超时都归入此分支, 换下一形态重试
                logger.warning(f"douyin detail API ({name}) failed for {video_id}: {e!r}")
                continue
            if response.content:
                logger.debug(f"douyin detail API ({name}) succeeded for {video_id}")
                break
        return response

    async def parse_slides(self, video_id: str):
        from . import slides

        response = await self._request_detail(video_id)
        if response is None or not response.content:
            raise ParseException(
                f"douyin detail API unavailable for {video_id} "
                "after open-api/signed/bytespider attempts (likely risk-controlled)"
            )

        try:
            aweme_detail = slides.decode_aweme_detail(response.content)
        except Exception as e:
            # decode 失败可能是字段结构变更或返回了非 JSON 错误页
            preview = response.content[:200]
            logger.warning(
                f"decode douyin detail failed for {video_id}: {e!r} len={len(response.content)} preview={preview!r}"
            )
            raise ParseException(f"decode douyin detail failed for {video_id}: {e}") from e
        if aweme_detail is None:
            raise ParseException(f"can't find aweme_detail in PC detail API: {video_id}")

        contents = []

        # 添加图片内容 (纯静态图, 实况照片由 dynamic_urls 单独处理)
        if image_urls := aweme_detail.image_urls:
            contents.extend(self.create_image_contents(image_urls))

        # 添加动态内容 (实况照片对应的 mp4 视频)
        if dynamic_urls := aweme_detail.dynamic_urls:
            contents.extend(
                self.create_dynamic_contents(
                    dynamic_urls,
                    cover_urls=aweme_detail.dynamic_cover_urls,
                    bgm_url=aweme_detail.bgm_url,
                )
            )

        # 普通视频 (images/dynamic 均空, 顶层 video 含 play_addr)
        # 自抖音 2026-08 改版, 普通视频改由 detail API 提供直链。
        # `if not contents` 确保仅纯视频进此分支, 图文/实况不受影响。
        if not contents and (video_url := aweme_detail.video_url):
            contents.append(self.create_video_content(video_url, aweme_detail.cover_url, aweme_detail.duration_ms))

        # 构建作者
        author = self.create_author(aweme_detail.name, aweme_detail.avatar_url)

        return self.result(
            title=aweme_detail.desc,
            author=author,
            contents=contents,
            # SlidesData 是秒, PictureSlidesData 是毫秒, 用统一 property 兜齐
            timestamp=aweme_detail.create_time_seconds,
        )
