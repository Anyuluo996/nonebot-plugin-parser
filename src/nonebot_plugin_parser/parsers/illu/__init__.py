"""ILLU 解析器。

适配自 parser-lite 的 illu。解析文章(Article)与图集(Drawing)。
使用 Bmob 签名 API（encrypt.py）。
"""

import re
from typing import ClassVar

from msgspec import ValidationError

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .models import Detail, BizType
from .encrypt import sign_header
from .._format import format_num
from .articleByIdV2 import ArticleByIdV2, fetch_html_text_from_zip
from .articleByIdV2 import decoder as article_decoder
from .drawingDetail import DrawingDetail
from .drawingDetail import decoder as drawing_decoder

_API_BASE = "https://api.illund.com"


class IlluParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.ILLU, display_name="ILLU")

    def __init__(self):
        super().__init__()
        self.headers.update({"Referer": "https://illund.com/", "Origin": "https://illund.com"})

    async def _fetch_detail(self, object_id: str, biz_type: BizType):
        if biz_type is BizType.Article:
            router = Detail.ArticleDetail.value
            payload = {"articleId": object_id}
            decoder = article_decoder
        elif biz_type is BizType.Drawing:
            router = Detail.DrawingDetail.value
            payload = {"mainId": object_id}
            decoder = drawing_decoder
        else:
            raise ValueError(f"unsupported BizType: {biz_type!r}")

        resp = await self.request(
            f"{_API_BASE}{router}",
            method="POST",
            json=payload,
            headers=sign_header(router),
        )
        resp.raise_for_status()
        data = resp.json()["result"]
        try:
            return decoder.decode(data)
        except ValidationError as e:
            raise ParseException(str(data)) from e

    # 文章
    @handle("illund.com/share.html", r"articleId%3D(?P<articleId>[0-9a-z]+)")
    async def parse_article(self, searched: re.Match[str]):
        object_id = searched.group("articleId")
        result: ArticleByIdV2 = await self._fetch_detail(object_id, BizType.Article)
        detail = result.dataObject
        text_lines = await fetch_html_text_from_zip(self, detail.contentFile)

        return self.result(
            title=detail.title,
            graphics=text_lines,
            timestamp=detail.publishDate.timestamp,
            url=(
                "https://illund.com/share.html?al=mindlib%3A%2F%2Freactbox%2F"
                f"%3FarticleId%3D{object_id}%26hideTitleBar%3D1"
                "%26pagename%3DArticleDetailVC"
            ),
            author=self.create_author(
                name=detail.author.nickname,
                avatar_url=detail.author.headerImage.url,
            ),
            extra={
                "info": (
                    f"阅读 {format_num(detail.readCount)} | "
                    f"赞 {format_num(detail.thumbUpCount)} | "
                    f"评 {format_num(detail.commentCount)}"
                ),
                "reward_coin": format_num(detail.rewardCoin),
            },
        )

    # 图集
    @handle("illund.com/share.html", r"mainid%3D(?P<mainId>[0-9a-z]+)")
    async def parse_drawing(self, searched: re.Match[str]):
        object_id = searched.group("mainId")
        detail: DrawingDetail = await self._fetch_detail(object_id, BizType.Drawing)

        graphics: list = []
        if detail.content:
            graphics.append(detail.content)
        for img in detail.images:
            graphics.append(self.create_image_content(img.url))

        return self.result(
            title=detail.title,
            graphics=graphics,
            timestamp=detail.publishDate.timestamp,
            url=(
                "https://illund.com/share.html?al=mindlib%3A%2F%2Freactbox%2F"
                f"%3Fmainid%3D{object_id}%26hideTitleBar%3D1"
                "%26pagename%3DDrawingDetailVC"
            ),
            author=self.create_author(
                name=detail.author.nickname,
                avatar_url=detail.author.headerImage.url,
            ),
            extra={
                "info": (
                    f"阅读 {format_num(detail.readCount)} | "
                    f"赞 {format_num(detail.likeCount)} | "
                    f"藏 {format_num(detail.collectCount)} | "
                    f"评 {format_num(detail.commentCount)}"
                ),
                "reward_coin": format_num(detail.rewardCoin),
            },
        )
