"""百度贴吧解析器。

适配自 parser-lite 的 tieba。通过 protobuf 协议获取帖子主楼 + 回复。
主楼 + 前几楼回复一起渲染为长图（extra['posts']，专用渲染器）。
"""

import re
from typing import ClassVar

from ..base import Platform, BaseParser, PlatformEnum, handle
from .utils import get_post, build_content, build_reply_floors
from .._format import format_num


class TiebaParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.TIEBA, display_name="百度贴吧"
    )

    @handle("tieba.baidu.com", r"tieba\.baidu\.com/p/(?P<post_id>\d+)")
    async def _parse(self, searched: re.Match[str]):
        post_id = searched.group("post_id")
        posts = await get_post(self, int(post_id))

        thread = posts.thread
        forum = posts.forum

        author = self.create_author(
            name=thread.user.show_name,
            avatar_url=f"http://tb.himg.baidu.com/sys/portraith/item/{thread.user.portrait}",
        )

        contents = build_content(posts, self.create_image_content, self.create_video_content)

        # 前几楼回复，渲染进长图（与 NGA/知乎对齐的多楼层模式）
        reply_floors = build_reply_floors(
            posts, self.create_image_content, self.create_video_content
        )

        return self.result(
            title=thread.title,
            graphics=contents,
            timestamp=thread.create_time,
            url=f"https://tieba.baidu.com/p/{post_id}",
            author=author,
            extra={
                "info": (
                    f"浏览 {format_num(thread.view_num)} | "
                    f"赞 {format_num(thread.agree)} | "
                    f"回复 {format_num(thread.reply_num)}"
                ),
                "forum": forum.fname,
                "reply_count": len(posts.objs) - 1 if len(posts.objs) > 1 else 0,
                "posts": reply_floors,
            },
        )
