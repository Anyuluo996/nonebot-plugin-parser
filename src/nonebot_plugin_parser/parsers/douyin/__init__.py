import re
from typing import ClassVar

from nonebot import logger

from ..base import (
    Platform,
    BaseParser,
    PlatformEnum,
    ParseException,
    handle,
)

# PC web 详情接口用的新版 UA，避免 COMMON_HEADER 里 2016 年的 Chrome/55 UBrowser
# 直接被抖音风控识别为异常客户端。仅作用于 parse_slides，不改全局 COMMON_HEADER
# （后者被各 parser / 下载器复用，贸然升级可能影响其它平台）。
_PC_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


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

        # slides / note 可能是纯图文, 也可能是含实况照片(live photo)的图文:
        # 后者重定向成 note/ 或 slides/, 而 parse_video 依赖的 _ROUTER_DATA
        # 不返回 images[].video, 会丢失实况视频; PC detail API 才返回。
        # 因此 slides / note 优先走 parse_slides, 失败再回退 parse_video (兼容纯视频/旧结构)。
        if ty in ("slides", "note"):
            try:
                return await self.parse_slides(vid)
            except Exception as e:
                # 注意: msgspec.DecodeError 不是 ParseException (它继承 ValueError),
                # 故这里用 Exception 兜住所有异常 —— PC detail 接口被风控返回空 body 时
                # decode 会抛 DecodeError, 必须落到 parse_video 兜底, 否则直接 traceback。
                logger.warning(f"parse_slides failed for {ty} {vid}, fallback to parse_video: {e!r}")

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
        # 注入 ttwid Cookie (从 .env 的 parser_douyin_ttwid 读取):
        # 抖音对 detail 接口有强风控, 无 ttwid 时返回空 body, 实况照片视频无法解析。
        # 配置有效 ttwid 后可恢复实况照片解析; 留空则纯图文仍可走 parse_video 兜底。
        from ...config import pconfig

        if ttwid := pconfig.douyin_ttwid:
            headers["Cookie"] = f"ttwid={ttwid}"
        params = {"aweme_id": video_id, "aid": "6383"}
        response = await self.request(detail_url, headers=headers, params=params)

        # 空 body 防御: 抖音风控下 detail 接口常返回 200 + content-length: 0,
        # 此时 msgspec 解码会抛 DecodeError(继承 ValueError), 这里主动转成
        # ParseException, 由上层 note fallback 捕获后回退 parse_video。
        if not response.content:
            raise ParseException(f"douyin detail API returned empty body for {video_id} (likely risk-controlled)")

        try:
            aweme_detail = slides.detail_decoder.decode(response.content).aweme_detail
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
                )
            )

        # 构建作者
        author = self.create_author(aweme_detail.name, aweme_detail.avatar_url)

        return self.result(
            title=aweme_detail.desc,
            author=author,
            contents=contents,
            timestamp=aweme_detail.create_time,
        )
