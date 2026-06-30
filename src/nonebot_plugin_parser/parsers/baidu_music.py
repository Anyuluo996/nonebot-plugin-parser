"""百度音乐解析器（基于 Meting-API）。

> ⚠️ 实验性：未实测成功，可能受百度接口限制。

支持 music.baidu.com 链接，需配置 parser_meting_api。
"""

import re
from typing import ClassVar

from .base import Platform, PlatformEnum, handle
from .meting_base import MetingBaseParser


class BaiduMusicParser(MetingBaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.BAIDU_MUSIC, display_name="百度音乐")
    _meting_server: ClassVar[str] = "baidu"

    def _extract_song_id(self, searched) -> str:
        return searched.group("song_id")

    @handle(
        "music.baidu.com",
        r"music\.baidu\.com/song/(?P<song_id>\d+)",
    )
    async def _parse_baidu(self, searched: re.Match[str]):
        song_id = self._extract_song_id(searched)
        return await self._parse_by_song_id(song_id, share_url=f"https://music.baidu.com/song/{song_id}")
