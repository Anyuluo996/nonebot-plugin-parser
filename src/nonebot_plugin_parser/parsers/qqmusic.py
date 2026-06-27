"""QQ音乐解析器（基于 Meting-API）。

支持 y.qq.com 链接，需配置 parser_meting_api。
"""

import re
from typing import ClassVar

from .base import Platform, PlatformEnum, handle
from .meting_base import MetingBaseParser


class QQMusicParser(MetingBaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.QQMUSIC, display_name="QQ音乐"
    )
    _meting_server: ClassVar[str] = "tencent"

    def _extract_song_id(self, searched) -> str:
        return searched.group("song_id")

    @handle(
        "y.qq.com",
        r"y\.qq\.com.*?/songDetail/\?songmid=(?P<song_id>[a-zA-Z0-9]+)",
    )
    @handle(
        "i.y.qq.com",
        r"i\.y\.qq\.com.*?songmid=(?P<song_id>[a-zA-Z0-9]+)",
    )
    async def _parse_qqmusic(self, searched: re.Match[str]):
        song_id = self._extract_song_id(searched)
        return await self._parse_by_song_id(
            song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}"
        )
