"""网易云音乐解析器（直连官方接口，开箱即用，无需配置）。

支持链接格式：
- 长链 music.163.com/#/song?id=X、y.music.163.com/m/song?id=X
- 短链 163cn.tv/xxxxxx（需重定向解析）

数据源为网易云公开 Web 接口（见 .api），不依赖 Meting-API 外部容器。
"""

import re
from typing import ClassVar

from . import api as netease_api
from ..base import Platform, BaseParser, PlatformEnum, IgnoreException, handle

_NETEASE_SONG_RE = re.compile(
    r"music\.163\.com.*?(?:/song(?:\?|/)|[?&]id=)(?P<song_id>\d+)"
)


class NCMParser(BaseParser):
    """网易云音乐解析器（直连，无需 Meting）。"""

    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.NETEASE, display_name="网易云音乐"
    )

    async def _parse_by_song_id(self, song_id: str, share_url: str):
        """统一解析流程：详情 → 播放地址 → 歌词 + 封面。"""
        detail = await netease_api.get_song_detail(self, song_id)
        if not detail:
            raise IgnoreException("未找到该歌曲")

        audio_url = await netease_api.get_song_url(self, song_id)
        if not audio_url:
            raise IgnoreException("无法获取音频下载地址（可能为 VIP/无版权）")

        contents = [self.create_audio_content(audio_url, duration=detail["duration"])]

        # 封面走渲染专用字段（cover_image），不入 contents，不会被发送
        cover_image = None
        if detail["pic_url"]:
            cover_image = self.create_cover_image_task(detail["pic_url"])

        lyric = await netease_api.get_lyric(self, song_id)

        return self.result(
            title=detail["name"],
            author=self.create_author(detail["artist"]),
            url=share_url,
            contents=contents,
            cover_image=cover_image,
            extra={"lyric": lyric},
        )

    @handle(
        "music.163.com",
        r"music\.163\.com.*?(?:/song(?:\?|/)|[?&]id=)(?P<song_id>\d+)",
    )
    async def _parse_netease(self, searched: re.Match[str]):
        song_id = searched.group("song_id")
        return await self._parse_by_song_id(
            song_id, share_url=f"https://music.163.com/song/{song_id}"
        )

    @handle("163cn.tv", r"https?://[^\s]*?163cn\.tv/(?P<code>[a-zA-Z0-9]+)")
    async def _parse_163cn_short(self, searched: re.Match[str]):
        short_url = searched.group(0)
        resp = await self.request(short_url, follow_redirects=True, raise_for_status=False)
        final_url = str(resp.url)
        if not (m := _NETEASE_SONG_RE.search(final_url)):
            raise IgnoreException(f"无法从短链解析歌曲 id: {final_url}")
        song_id = m.group("song_id")
        return await self._parse_by_song_id(
            song_id, share_url=f"https://music.163.com/song/{song_id}"
        )
