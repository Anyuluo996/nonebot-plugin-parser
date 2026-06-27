"""虎扑解析器。

适配自 parser-lite 的 hupu。解析虎扑 BBS 帖子主楼 + 回复。
评论数据放入 extra（本项目暂不渲染评论）。
"""

import re
from typing import ClassVar

from .bbs import decoder, parse_rich_content
from ..base import Platform, BaseParser, PlatformEnum, handle
from .._format import format_num


class HupuParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.HUPU, display_name="虎扑")

    @handle("m.hupu.com/bbs-share", r"bbs-share/(?P<topic_id>\d+)(?:\.html)?")
    @handle("bbs.hupu.com", r"(?P<topic_id>\d+)(?:\.html)?")
    @handle("m.hupu.com/bbs", r"bbs/(?P<topic_id>\d+)(?:\.html)?")
    async def parse_bbs(self, searched: re.Match[str]):
        topic_id = searched.group("topic_id")
        res = await self.request(
            f"https://m.hupu.com/api/v1/bbs-thread-frontend/{topic_id}"
        )
        data = decoder.decode(res.content).data
        bbs = data.t_detail

        graphics = parse_rich_content(bbs.html, self.create_image_content)

        return self.result(
            title=bbs.title,
            graphics=graphics,
            timestamp=bbs.timestamp,
            url=f"https://bbs.hupu.com/{bbs.tid}",
            author=self.create_author(
                name=bbs.user.username,
                avatar_url=bbs.user.header,
                description=bbs.via,
            ),
            extra={
                "info": (
                    f"浏览 {format_num(bbs.hits)} | "
                    f"亮 {format_num(bbs.lights)} | "
                    f"回复 {format_num(bbs.replies)}"
                ),
                "forum": bbs.f_info.f_name,
                "reply_count": len(data.r_list),
            },
        )
