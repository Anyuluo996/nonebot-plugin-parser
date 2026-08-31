import re
from typing import Literal, ClassVar

from msgspec import Struct, field
from nonebot import logger
from msgspec.json import Decoder

from .base import BaseParser, PlatformEnum, handle
from .data import Platform, ParseResult, ImageContent, MediaContent


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


# ---- fxtwitter 文章结构 (X Articles, vxtwitter 只返回预览, 全文在 fxtwitter) ----


class FxMediaInfo(Struct):
    typename: str | None = field(default=None, name="__typename")
    original_img_url: str | None = None


class FxMediaEntity(Struct):
    media_id: str
    media_info: FxMediaInfo | None = None


class FxCoverMedia(Struct):
    media_info: FxMediaInfo | None = None


class FxMediaItem(Struct):
    media_id: str = field(name="mediaId")


class FxEntityValueData(Struct):
    media_items: list[FxMediaItem] = field(default_factory=list, name="mediaItems")
    tweet_id: str | None = field(default=None, name="tweetId")


class FxEntityValue(Struct):
    type: str | None = None
    data: FxEntityValueData = field(default_factory=FxEntityValueData)


class FxEntity(Struct):
    key: str
    value: FxEntityValue


class FxEntityRange(Struct):
    key: int
    offset: int = 0
    length: int = 0


class FxBlock(Struct):
    type: str
    text: str = ""
    entityRanges: list[FxEntityRange] = field(default_factory=list)


class FxArticleContent(Struct):
    blocks: list[FxBlock] = field(default_factory=list)
    entityMap: list[FxEntity] = field(default_factory=list)


class FxArticle(Struct):
    title: str | None = None
    preview_text: str | None = None
    cover_media: FxCoverMedia | None = None
    content: FxArticleContent = field(default_factory=FxArticleContent)
    media_entities: list[FxMediaEntity] = field(default_factory=list)


class FxTweet(Struct):
    article: FxArticle | None = None


class FxTwitterResponse(Struct):
    tweet: FxTweet | None = None


fx_decoder = Decoder(FxTwitterResponse)

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

        # 长文(文章)推文: vxtwitter 只给预览, 回源 fxtwitter 拿全文
        article: FxArticle | None = None
        if isinstance(data.article, Article):
            try:
                article = await self._fetch_fx_article(url)
            except Exception as e:
                logger.warning(f"获取 X 文章全文失败, 降级为预览: {e!r}")

        return self._collect_result(data, article)

    async def _fetch_fx_article(self, url: str) -> FxArticle | None:
        api_url = url.replace("x.com", "api.fxtwitter.com")
        response = await self.request(api_url)
        data = fx_decoder.decode(response.content)
        return data.tweet.article if data.tweet else None

    def _article_to_graphics(self, article: FxArticle) -> list[str | ImageContent]:
        """文章 blocks 转图文列表: 文本段落 + 内嵌图片(组), 供渲染器长文分页"""
        graphics: list[str | ImageContent] = []
        if article.cover_media and article.cover_media.media_info:
            if cover_url := article.cover_media.media_info.original_img_url:
                graphics.append(self.create_image_content(cover_url))

        media_by_id = {m.media_id: m for m in article.media_entities}
        entities = {e.key: e.value for e in article.content.entityMap}
        for block in article.content.blocks:
            if block.type == "atomic":
                for entity_range in block.entityRanges:
                    entity = entities.get(str(entity_range.key))
                    if entity is None:
                        continue
                    if entity.type == "MEDIA":
                        for item in entity.data.media_items:
                            media = media_by_id.get(item.media_id)
                            if media and media.media_info and media.media_info.original_img_url:
                                graphics.append(self.create_image_content(media.media_info.original_img_url))
                    elif entity.type == "TWEET" and entity.data.tweet_id:
                        graphics.append(f"[内嵌推文] https://x.com/i/web/status/{entity.data.tweet_id}")
            elif text := block.text.strip():
                graphics.append(text)
        return graphics

    def _collect_result(
        self,
        data: VxTwitterResponse,
        article: FxArticle | None = None,
        depth: int = 0,
    ) -> ParseResult:
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

        # 文章推文: 正文 + 内嵌图全部放进 graphics, 由渲染器分页出长图;
        # text 里的原始内容只是文章链接, 丢弃
        graphics: list[str | ImageContent] = []
        text = data.text
        if article is not None:
            graphics = self._article_to_graphics(article)
            text = None
        elif isinstance(data.article, Article) and data.article.preview_text:
            # fxtwitter 拿不到全文时至少展示预览
            text = data.article.preview_text

        # 限制转发链递归深度，防止循环引用/极深嵌套导致 RecursionError 崩溃
        repost = self._collect_result(data.qrt, depth=depth + 1) if data.qrt and depth < MAX_REPOST_DEPTH else None

        return self.result(
            author=author,
            title=title,
            text=text,
            timestamp=data.date_epoch,
            contents=contents,
            graphics=graphics,
            repost=repost,
        )
