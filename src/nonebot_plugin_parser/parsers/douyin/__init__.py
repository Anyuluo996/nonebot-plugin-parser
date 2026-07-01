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

# PC web 详情接口用的新版 UA，避免 COMMON_HEADER 里 2016 年的 Chrome/55 UBrowser
# 直接被抖音风控识别为异常客户端。仅作用于 parse_slides，不改全局 COMMON_HEADER
# （后者被各 parser / 下载器复用，贸然升级可能影响其它平台）。
_PC_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ABogus 签名器实例。内部状态在 get_value 调用时会 reset，实例可安全复用，
# 避免每次 parse_slides 都重建对象（SM3 表/浏览器信息等初始化开销）。
_ABOGUS = ABogus()

# PC web 详情接口的通用请求参数（仿抖音 web 端 getCommonData）。
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

    # https://v.douyin.com/_2ljF4AmKL8
    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    # https://www.douyin.com/video/7521023890996514083
    # https://www.douyin.com/note/7469411074119322899
    @handle("douyin", r"douyin\.com/(?P<ty>video|note)/(?P<vid>\d+)")
    @handle("iesdouyin", r"iesdouyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    @handle("m.douyin", r"m\.douyin\.com/share/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    # https://jingxuan.douyin.com/m/video/7574300896016862490?app=yumme&utm_source=copy_link
    @handle("jingxuan.douyin", r"jingxuan\.douyin.com/m/(?P<ty>slides|video|note)/(?P<vid>\d+)")
    async def _parse_douyin(self, searched: re.Match[str]):
        ty, vid = searched.group("ty"), searched.group("vid")

        # slides 类型无可用兜底 (m/iesdouyin 分享页均无 _ROUTER_DATA),
        # 保持原行为: 走 parse_slides, 失败直接 ParseException, 不做无效兜底。
        if ty == "slides":
            return await self.parse_slides(vid)

        # note 可能是纯图文, 也可能是含实况照片(live photo)的图文:
        # 后者重定向成 note/ 而非 slides/, 而 parse_video 依赖的 _ROUTER_DATA
        # 不返回 images[].video, 会丢失实况视频; PC detail API 才返回。
        # 因此 note 优先走 parse_slides, 失败再回退 parse_video (兼容纯视频/旧结构)。
        # 捕获范围: ParseException + msgspec.DecodeError (继承 ValueError) +
        # ValueError (兜住 msgspec 结构变更) + httpx/timeout 错误。
        if ty == "note":
            try:
                return await self.parse_slides(vid)
            except (ParseException, ValueError) as e:
                # ParseException: parse_slides 内部主动 raise 的业务错误 (含空 body 防御)
                # ValueError: 兜住 msgspec.DecodeError (风控下空 body 解码失败)
                # 不再用 bare Exception, 避免吞掉 AttributeError / TypeError 等代码 bug
                # exc_info=True 保留 traceback, 便于事后排查非预期异常
                logger.warning(
                    f"parse_slides failed for note {vid}, fallback to parse_video: {e!r}",
                    exc_info=True,
                )

        # 兜底: 走 m站 / iesdouyin 分享页的 _ROUTER_DATA
        # 注意 parse_video 对 live photo 只能拿到静态图(images[].video 丢失),
        # 这是风控下 PC detail 接口不可用时的降级, 比直接 traceback 好。
        for url in (self._build_m_douyin_url(ty, vid), self._build_iesdouyin_url(ty, vid)):
            try:
                return await self.parse_video(url)
            except ParseException as e:
                logger.warning(f"failed to parse {url}, error: {e}")
                continue
        raise ParseException("分享已删除或资源直链提取失败, 请稍后再试")

    @staticmethod
    def _build_iesdouyin_url(ty: str, vid: str) -> str:
        return f"https://www.iesdouyin.com/share/{ty}/{vid}"

    @staticmethod
    def _build_m_douyin_url(ty: str, vid: str) -> str:
        return f"https://m.douyin.com/share/{ty}/{vid}"

    async def parse_video(self, url: str):
        from . import video

        response = await self.request(
            url,
            headers=self.ios_headers,
            follow_redirects=False,
            raise_for_status=False,
        )
        if response.status_code != 200:
            raise ParseException(f"status: {response.status_code}")
        text = response.text

        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        matched = pattern.search(text)

        if not matched or not matched.group(1):
            raise ParseException("can't find _ROUTER_DATA in html")

        video_data = video.decoder.decode(matched.group(1).strip()).video_data
        # 使用新的简洁构建方式
        contents = []

        # 添加图片内容
        if image_urls := video_data.image_urls:
            contents.extend(self.create_image_contents(image_urls))

        # 添加视频内容
        elif video_url := video_data.video_url:
            cover_url = video_data.cover_url
            duration = video_data.video.duration if video_data.video else 0
            contents.append(self.create_video_content(video_url, cover_url, duration))

        # 构建作者
        author = self.create_author(video_data.author.nickname, video_data.avatar_url)

        return self.result(
            title=video_data.desc,
            author=author,
            contents=contents,
            timestamp=video_data.create_time,
        )

    async def parse_slides(self, video_id: str):
        from . import slides

        # 优先使用 PC web detail API, 它能返回实况照片(live photo)的视频地址
        # 旧的 slidesinfo v2 API 返回的 images 不含 video 字段, 实况照片会丢失
        detail_url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        # PC web 接口对新版 UA 更友好; Referer / X-Requested-With 缺一不可,
        # 否则抖音风控会返回 HTTP 200 + 空 body (而非 4xx), 导致后续 decode 失败。
        headers = {
            **self.headers,
            "User-Agent": _PC_WEB_UA,
            "Referer": "https://www.douyin.com/",
            "X-Requested-With": "XMLHttpRequest",
        }
        # 注入登录态凭据: 抖音 detail 接口要求登录态凭据 + a_bogus 签名配套才放行,
        # 仅 a_bogus 或游客态 ttwid 都会被风控拦截返回空 body。
        # 凭据优先级: 完整 cookie (dycookie 指令 / parser_douyin_cookie) > 仅 ttwid
        # (dyttwid 指令 / parser_douyin_ttwid), 详见 ttwid.get_effective_credential。
        # 完整 cookie 含 sessionid/sid_guard 等登录态字段, 抗风控能力远强于仅 ttwid。
        from . import ttwid as dy_ttwid

        if credential := dy_ttwid.get_effective_credential():
            headers["Cookie"] = credential
        # 组装请求参数: 通用参数 + aweme_id + msToken + a_bogus 签名。
        # - msToken: 128 位随机 hex, 仿浏览器随机生成 (真实 msToken 由服务端下发,
        #   这里伪造占位, 抖音仅校验存在性与长度)。
        # - a_bogus: 由 ABogus 算法对所有参数计算得出的签名, 缺失则必被风控。
        #   必须最后追加, 且需 url 编码 (签名串含 = 等特殊字符)。
        params = {
            **_PC_WEB_COMMON_PARAMS,
            "aweme_id": video_id,
            "msToken": secrets.token_hex(64),
        }
        a_bogus = quote(_ABOGUS.get_value(params), safe="")
        params["a_bogus"] = a_bogus
        response = await self.request(detail_url, headers=headers, params=params)

        # 空 body 防御: 抖音风控下 detail 接口常返回 200 + content-length: 0,
        # 此时 msgspec 解码会抛 DecodeError(继承 ValueError), 这里主动转成
        # ParseException, 由上层 note fallback 捕获后回退 parse_video。
        if not response.content:
            raise ParseException(f"douyin detail API returned empty body for {video_id} (likely risk-controlled)")

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

        # 构建作者
        author = self.create_author(aweme_detail.name, aweme_detail.avatar_url)

        return self.result(
            title=aweme_detail.desc,
            author=author,
            contents=contents,
            # SlidesData 是秒, PictureSlidesData 是毫秒, 用统一 property 兜齐
            timestamp=aweme_detail.create_time_seconds,
        )
