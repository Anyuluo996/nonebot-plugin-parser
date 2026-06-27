"""小黑盒解析器。

适配自 parser-lite 的 heybox。使用 hkey 签名 API（encrypt.py）。
parser-lite 原实现需浏览器跑 JS 取 x_xhh_tokenid；本项目无浏览器 JS 执行能力，
故不携带 token（匿名访问，部分帖子可能受限）。
"""

import re
from typing import ClassVar

from msgspec import convert

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .model import BaseResult
from .encrypt import build_url
from .._format import format_num


class HeyBoxParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.HEYBOX, display_name="小黑盒"
    )

    def __init__(self):
        super().__init__()
        self.headers.update(
            {
                "Referer": "https://www.xiaoheihe.cn/",
                "Host": "api.xiaoheihe.cn",
                "Origin": "https://www.xiaoheihe.cn",
                "Accept": "application/json, text/plain, */*",
            }
        )

    @handle("xiaoheihe.cn/app/bbs", r"link\/(?P<link_id>[A-Za-z0-9]+)")
    @handle("xiaoheihe.cn/bbs/post_share", r"link_id=(?P<link_id>[A-Za-z0-9]+)")
    async def _parse(self, searched: re.Match[str]):
        link_id = searched.group("link_id")

        response = await self.request(build_url(link_id), headers=self.headers)
        response.raise_for_status()
        res = response.json()

        if res.get("status") != "ok":
            raise ParseException(f"小黑盒解析失败: {res}")

        data = convert(res["result"], BaseResult)
        link = data.link

        graphics = link.to_graphics(
            self.create_image_content, self.create_video_content
        )

        return self.result(
            title=link.title,
            graphics=graphics,
            timestamp=link.create_at,
            url=f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}",
            author=self.create_author(
                name=link.user.username,
                avatar_url=link.user.avatar_url,
            ),
            extra={
                "info": (
                    f"浏览 {format_num(link.click)} | "
                    f"赞 {format_num(link.link_award_num)} | "
                    f"藏 {format_num(link.favour_count)} | "
                    f"评 {format_num(link.comment_num)}"
                ),
            },
        )
