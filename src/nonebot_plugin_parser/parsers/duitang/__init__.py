"""堆糖解析器。

适配自 parser-lite 的 duitang。解析 blog/atlas 图集。
"""

import re
from typing import ClassVar

from msgspec import convert

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .model import BlogData, AtlasData
from .._format import format_num


class DuiTangParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.DUITANG, display_name="堆糖")

    @handle("duitang.com/blog", r"duitang\.com/blog/?\?id=(?P<id>\d+)")
    async def parse_blog(self, searched: re.Match[str]):
        blog_id = searched.group("id")
        blog_data = await self._fetch_blog_detail(blog_id)

        graphics: list = []
        if blog_data.msg:
            graphics.append(blog_data.msg)
        graphics.append(self.create_image_content(blog_data.photo.path))

        return self.result(
            graphics=graphics,
            timestamp=blog_data.add_datetime_ts,
            url=f"https://m.duitang.com/blog?id={blog_id}",
            author=self.create_author(
                name=blog_data.sender.username,
                avatar_url=blog_data.sender.avatar,
            ),
            extra={
                "info": (
                    f"赞 {format_num(blog_data.like_count)} | "
                    f"藏 {format_num(blog_data.favorite_count)} | "
                    f"评 {format_num(blog_data.reply_count)}"
                ),
            },
        )

    @handle("duitang.com/atlas", r"duitang\.com/atlas/?\?id=(?P<id>\d+)")
    async def parse_atlas(self, searched: re.Match[str]):
        atlas_id = searched.group("id")
        atlas_data = await self._fetch_atlas_detail(atlas_id)

        graphics: list = []
        if atlas_data.desc:
            graphics.append(atlas_data.desc)
        for url in atlas_data.img_list:
            graphics.append(self.create_image_content(url))

        return self.result(
            graphics=graphics,
            timestamp=atlas_data.created_at // 1000,
            url=f"https://www.duitang.com/atlas?id={atlas_id}",
            author=self.create_author(
                name=atlas_data.sender.username,
                avatar_url=atlas_data.sender.avatar,
            ),
            extra={
                "info": (
                    f"浏览 {format_num(atlas_data.visit_count)} | "
                    f"赞 {format_num(atlas_data.like_count)} | "
                    f"藏 {format_num(atlas_data.favorite_count)} | "
                    f"评 {format_num(atlas_data.comment_count)}"
                ),
            },
        )

    async def _fetch_atlas_detail(self, atlas_id: str) -> AtlasData:
        resp = await self.request(
            "https://www.duitang.com/napi/vienna/atlas/detail/",
            params={"atlas_id": atlas_id},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 1:
            raise ParseException(f"堆糖接口错误: {data}")
        return convert(data["data"], AtlasData)

    async def _fetch_blog_detail(self, blog_id: str) -> BlogData:
        resp = await self.request(
            "https://www.duitang.com/napi/blog/with_instance_tag/detail/",
            params={
                "blog_id": blog_id,
                "include_fields": (
                    "tags,related_albums,related_albums.covers,root_album,"
                    "share_links_2,extra_links,icon_description,root_id"
                ),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 1:
            raise ParseException(f"堆糖接口错误: {data}")
        return convert(data["data"], BlogData)
