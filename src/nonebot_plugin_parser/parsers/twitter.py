import re
from typing import Literal, ClassVar

from msgspec import Struct, field
from msgspec.json import Decoder

from .base import BaseParser, PlatformEnum, handle
from .data import Platform, ParseResult, MediaContent


class MediaElement(Struct):
    type: Literal["video", "image", "gif"]
    url: str
    altText: str | None = None
    thumbnail_url: str | None = None
    duration_millis: int | None = None


class Article(Struct):
    image: str | None = None
    preview_text: str | None = None
    title: str | None = None


class VxTwitterResponse(Struct):
    article: str | Article | None
    date_epoch: int
    fetched_on: int
    likes: int
    text: str
    user_name: str
    user_screen_name: str
    user_profile_image_url: str
    qrt: "VxTwitterResponse | None" = None
    qrtURL: str | None = None
    media_extended: list[MediaElement] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.user_name} @{self.user_screen_name}"


decoder = Decoder(VxTwitterResponse)

# 转发链递归深度上限，防止循环引用/极深嵌套导致 RecursionError 崩溃
MAX_REPOST_DEPTH = 5


class TwitterParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.TWITTER, display_name="小蓝鸟")

    @handle("x.com", r"x.com/[0-9-a-zA-Z_]{1,20}/status/([0-9]+)")
    async def _parse(self, searched: re.Match[str]) -> ParseResult:
        url = f"https://{searched.group(0)}"
        return await self.parse_by_vxapi(url)

    async def parse_by_vxapi(self, url: str):
        """使用 vxtwitter API 解析 Twitter 链接"""

        api_url = url.replace("x.com", "api.vxtwitter.com")
        response = await self.request(api_url)
        data = decoder.decode(response.content)
        return self._collect_result(data)

    def _collect_result(self, data: VxTwitterResponse, depth: int = 0) -> ParseResult:
        author = self.create_author(data.user_name, data.user_profile_image_url)
        title = data.article.title if isinstance(data.article, Article) else data.article

        contents: list[MediaContent] = []
        for media in data.media_extended:
            if media.type == "video":
                contents.append(self.create_video_content(media.url, media.thumbnail_url))
            elif media.type == "gif":
                contents.extend(
                    self.create_dynamic_contents(
                        [media.url],
                        convert_to_gif=True,
                        cover_url=media.thumbnail_url,
                    )
                )
            elif media.type == "image":
                contents.append(self.create_image_content(media.url))

        # 限制转发链递归深度，防止循环引用/极深嵌套导致 RecursionError 崩溃
        repost = self._collect_result(data.qrt, depth + 1) if data.qrt and depth < MAX_REPOST_DEPTH else None

        return self.result(
            author=author,
            title=title,
            text=data.text,
            timestamp=data.date_epoch,
            contents=contents,
            repost=repost,
        )
