"""酷狗音乐解析器（基于 Meting-API）。

> ⚠️ 实验性：未实测成功，酷狗接口可能受限。

支持分享链接，需配置 parser_meting_api。
"""

import re
from typing import ClassVar

from .base import Platform, PlatformEnum, handle
from .meting_base import MetingBaseParser


class KuGouParser(MetingBaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.KUGOU, display_name="酷狗音乐"
    )
    _meting_server: ClassVar[str] = "kugou"

    def _extract_song_id(self, searched) -> str:
        return searched.group("song_id")

    @handle(
        "kugou.com",
        r"https?://[^\s]*?kugou\.com.*?(?:/(?:share|mixsong)/[a-zA-Z0-9]+\.html|(?:id|chain)=[a-zA-Z0-9]+)",
    )
    async def _parse_kugou(self, searched: re.Match[str]):
        share_url = searched.group(0)
        # 酷狗分享链接需先重定向拿到真实 song hash
        resp = await self.request(share_url, follow_redirects=True, raise_for_status=False)
        final_url = str(resp.url)
        # 从重定向 URL 或页面提取 hash
        if m := re.search(r"hash=([a-zA-Z0-9]+)", final_url):
            song_id = m.group(1)
        elif m := re.search(r"songhash=([a-zA-Z0-9]+)", resp.text):
            song_id = m.group(1)
        else:
            raise ValueError("无法从酷狗链接提取歌曲 hash")
        return await self._parse_by_song_id(song_id, share_url=share_url)
