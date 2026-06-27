"""LOFTER 解析器。

适配自 parser-lite 的 lofter。解析 LOFTER 图文/音乐帖。
评论数据放入 extra（本项目暂不渲染评论）。
"""

import re
from typing import ClassVar

from msgspec import convert

from .post import Post
from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .._format import format_num


class LofterParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.LOFTER, display_name="LOFTER"
    )

    @handle("s.lofter.com", r"s\.lofter\.com/-s/[0-9A-Za-z]+")
    async def _parse_short_link(self, searched: re.Match[str]):
        short_url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(short_url)

    @handle(
        "lofter.com",
        r"post/(?P<blog_hex>[0-9a-zA-Z]+)_(?P<post_hex>[0-9a-zA-Z]+)",
    )
    async def _parser(self, searched: re.Match[str]):
        blog_id = int(searched.group("blog_hex"), 16)
        post_id = int(searched.group("post_hex"), 16)

        post_resp = await self.request(
            "https://api.lofter.com/oldapi/post/detail.api",
            method="POST",
            params={"product": "lofter-android-8.1.20"},
            data={"postid": post_id, "targetblogid": blog_id},
        )
        post_data = post_resp.json()

        meta = post_data.get("meta") or {}
        if meta.get("status") != 200:
            raise ParseException(
                f"Lofter 解析失败: {meta.get('msg', '未知错误')}"
            )

        post_raw = (post_data.get("response") or {}).get("posts") or []
        if not post_raw:
            raise ParseException("Lofter 解析失败: 未找到帖子内容")
        post = convert(post_raw[0]["post"], Post)

        graphics: list = []
        if post.text:
            graphics.append(post.text)
        for url in post.photo_urls:
            graphics.append(self.create_image_content(url))

        author = post.blogInfo
        stats = post.postCount

        return self.result(
            title=post.title,
            graphics=graphics,
            timestamp=post.publishTime // 1000,
            url=f"https://www.lofter.com{searched.group(0)}",
            author=self.create_author(
                name=author.blogNickName,
                avatar_url=author.bigAvaImg,
            ),
            extra={
                "info": (
                    f"赞 {format_num(stats.favoriteCount)} | "
                    f"评 {format_num(stats.responseCount)} | "
                    f"转 {format_num(stats.shareCount)}"
                ),
                "tags": post.tagList,
            },
        )
