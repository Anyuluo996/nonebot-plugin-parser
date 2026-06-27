"""QQ音乐解析器（基于 Meting-API）。

支持链接格式：
- 长链 y.qq.com/n/ryqq/songDetail/数字、y.qq.com/n/ryqq_v2/songDetail/数字
- 短链 c*.y.qq.com/base/fcgi-bin/u?__=xxx（需重定向解析）

需配置 parser_meting_api。
"""

import re
from typing import ClassVar

from .base import Platform, PlatformEnum, IgnoreException, handle
from .meting_base import MetingBaseParser

_QQ_SONGID_RE = re.compile(r"songDetail/(?:\?songmid=|)(?P<song_id>\w+)")


class QQMusicParser(MetingBaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.QQMUSIC, display_name="QQ音乐"
    )
    _meting_server: ClassVar[str] = "tencent"

    def _extract_song_id(self, searched) -> str:
        return searched.group("song_id")

    @handle(
        "y.qq.com",
        r"y\.qq\.com.*?songDetail/(?:\?songmid=|)(?P<song_id>[a-zA-Z0-9_]+)",
    )
    async def _parse_qqmusic(self, searched: re.Match[str]):
        song_id = self._extract_song_id(searched)
        return await self._parse_by_song_id(
            song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}"
        )

    @handle(
        "y.qq.com",
        r"https?://c\d+\.y\.qq\.com/base/fcgi-bin/u\?(?:[^ ]*?__=)?(?P<code>[\w]+)",
    )
    async def _parse_qqmusic_short(self, searched: re.Match[str]):
        """QQ音乐短跳转链接，跟随重定向拿到 songDetail 真实链接。"""
        short_url = searched.group(0)
        resp = await self.request(short_url, follow_redirects=True, raise_for_status=False)
        final_url = str(resp.url)
        if not (m := _QQ_SONGID_RE.search(final_url)):
            raise IgnoreException(f"无法从 QQ 音乐短链解析歌曲 id: {final_url}")
        song_id = m.group("song_id")
        return await self._parse_by_song_id(
            song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}"
        )
