"""网易云音乐解析器（基于 Meting-API）。

支持链接格式：
- 长链 music.163.com/#/song?id=X、y.music.163.com/m/song?id=X
- 短链 163cn.tv/xxxxxx（需重定向解析）

需配置 parser_meting_api。
"""

import re
from typing import ClassVar

from .base import Platform, PlatformEnum, handle
from .meting_base import MetingBaseParser

_NETEASE_SONG_RE = re.compile(
    r"music\.163\.com.*?(?:/song(?:\?|/)|[?&]id=)(?P<song_id>\d+)"
)


class NCMParser(MetingBaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.NETEASE, display_name="网易云音乐"
    )
    _meting_server: ClassVar[str] = "netease"

    def _extract_song_id(self, searched) -> str:
        return searched.group("song_id")

    @handle(
        "music.163.com",
        r"music\.163\.com.*?(?:/song(?:\?|/)|[?&]id=)(?P<song_id>\d+)",
    )
    async def _parse_netease(self, searched: re.Match[str]):
        song_id = self._extract_song_id(searched)
        return await self._parse_by_song_id(
            song_id, share_url=f"https://music.163.com/song/{song_id}", include_cover=True
        )

    @handle("163cn.tv", r"https?://[^\s]*?163cn\.tv/(?P<code>[a-zA-Z0-9]+)")
    async def _parse_163cn_short(self, searched: re.Match[str]):
        short_url = searched.group(0)
        resp = await self.request(short_url, follow_redirects=True, raise_for_status=False)
        final_url = str(resp.url)
        if not (m := _NETEASE_SONG_RE.search(final_url)):
            raise ValueError(f"无法从短链解析歌曲 id: {final_url}")
        song_id = m.group("song_id")
        return await self._parse_by_song_id(
            song_id, share_url=f"https://music.163.com/song/{song_id}", include_cover=True
        )
