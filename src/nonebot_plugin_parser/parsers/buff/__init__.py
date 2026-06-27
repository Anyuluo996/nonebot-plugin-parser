"""网易 BUFF 解析器。

适配自 parser-lite 的 buff。解析 news（图文）与 gallery（玩家秀）。
"""

import re
from typing import ClassVar

from msgspec import convert

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .models import News, Gallery, GalleryUser
from .._format import format_num


class BuffParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.BUFF, display_name="BUFF")

    async def _fetch_ok(self, url: str, params: dict, model, err_msg: str):
        resp = await self.request(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "OK":
            raise ParseException(f"{err_msg}: {data}")
        return convert(data["data"], model)

    @handle(
        "buff.163.com/s/news-detail_share.html",
        r"news-detail_share\.html\?.*?article_id=(?P<article_id>\d+).*?comment_type=211",
    )
    async def parse_news(self, searched: re.Match[str]):
        article_id = searched.group("article_id")
        news = await self._fetch_ok(
            "https://buff.163.com/api/news/share/detail",
            {"article_id": article_id},
            News,
            "获取 NEWS 信息失败",
        )
        return self.result(
            title=news.share_data.title,
            graphics=news.to_graphics(self.create_image_content),
            timestamp=news.publish_time,
            url=news.share_data.url,
            author=self.create_author(
                name=news.author, avatar_url=news.avatar, description=news.ip_location
            ),
            extra={
                "info": (
                    f"浏览 {format_num(news.views)} | "
                    f"赞 {format_num(news.ups_num)} | "
                    f"评 {format_num(news.replies)}"
                ),
            },
        )

    @handle(
        "buff.163.com/s/preview_share.html",
        r"preview_share\.html\?game=(?P<game>[a-z0-9]+)&preview_id=(?P<preview_id>[A-Za-z0-9]+)",
    )
    async def parse_gallery(self, searched: re.Match[str]):
        preview_id = searched.group("preview_id")
        game = searched.group("game")
        gallery = await self._fetch_ok(
            "https://buff.163.com/api/market/preview/share_detail",
            {"preview_id": preview_id, "game": game},
            Gallery,
            "获取玩家秀信息失败",
        )
        author: GalleryUser | None = gallery.user_infos.get(gallery.preview.user_id)
        graphics: list = []
        if gallery.preview.description:
            graphics.append(gallery.preview.description)
        graphics.append(self.create_image_content(gallery.preview.icon_url))
        return self.result(
            title=gallery.preview.share_data.title,
            graphics=graphics,
            timestamp=gallery.preview.publish_time,
            url=gallery.preview.share_data.url,
            author=self.create_author(
                name=author.nickname if author else "未知",
                avatar_url=author.avatar if author else "",
                description=author.ip_location if author else "",
            ),
            extra={"info": f"赞 {format_num(gallery.preview.ups_num)}"},
        )
