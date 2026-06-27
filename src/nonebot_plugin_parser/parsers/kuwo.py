"""酷我音乐解析器（基于 Meting-API）。

支持 play_detail 链接，需配置 parser_meting_api。
"""

import re
from typing import ClassVar

from .base import Platform, PlatformEnum, handle
from .meting_base import MetingBaseParser


class KuWoParser(MetingBaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.KUWO, display_name="酷我音乐"
    )
    _meting_server: ClassVar[str] = "kuwo"

    def _extract_song_id(self, searched) -> str:
        return searched.group("rid")

    @handle("kuwo.cn", r"kuwo\.cn/play_detail/(?P<rid>\d+)")
    async def _parse_kuwo(self, searched: re.Match[str]):
        rid = searched.group("rid")
        return await self._parse_by_song_id(
            rid, share_url=f"https://www.kuwo.cn/play_detail/{rid}"
        )
