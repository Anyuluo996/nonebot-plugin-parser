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
        if ty == "slides":
            return await self.parse_slides(vid)

        # note 可能是纯图文, 也可能是含实况照片(live photo)的图文:
        # 后者重定向成 note/ 而非 slides/, 而 parse_video 依赖的 _ROUTER_DATA
        # 不返回 images[].video, 会丢失实况视频; PC detail API 才返回。
        # 因此 note 优先走 parse_slides, 失败再回退 parse_video (兼容纯视频/旧结构)。
        if ty == "note":
            try:
                return await self.parse_slides(vid)
            except ParseException as e:
                logger.warning(f"parse_slides failed for note {vid}, fallback to parse_video: {e}")

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
        headers = {**self.headers, "Referer": "https://www.douyin.com/"}
        params = {"aweme_id": video_id, "aid": "6383"}
        response = await self.request(detail_url, headers=headers, params=params)

        aweme_detail = slides.detail_decoder.decode(response.content).aweme_detail
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
