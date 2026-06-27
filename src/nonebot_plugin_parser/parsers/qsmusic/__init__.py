"""汽水音乐解析器。

适配自 parser-lite 的 qsmusic，解析字节系汽水音乐分享链接。
从分享页 _ROUTER_DATA 提取歌曲资源与歌词。
"""

import re
from typing import ClassVar

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .share import decoder as shareDecoder

_ROUTER_DATA = re.compile(
    r'<script\s+async=""\s+data-script-src="modern-inline">_ROUTER_DATA\s*=\s*({[\s\S]*?});',
    flags=re.DOTALL,
)


class QSMusicParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.QSMUSIC, display_name="汽水音乐"
    )

    @handle(
        "qishui.douyin.com",
        r"https?://[^\s]*?qishui\.douyin\.com/s/[a-zA-Z0-9]+/",
    )
    async def _parse_qsmusic_share(self, searched: re.Match[str]):
        share_url = searched.group(0)

        resp = await self.request(share_url, headers=self.ios_headers)
        html = resp.text
        if matched := _ROUTER_DATA.search(html):
            raw = matched[1]
        else:
            raise ParseException("未找到结构化数据")

        music_data = shareDecoder.decode(
            raw
        ).loaderData.track_page.audioWithLyricsOption

        contents = [
            self.create_audio_content(music_data.url, duration=music_data.duration)
        ]
        if cover_url := music_data.coverURL:
            contents.append(self.create_image_content(cover_url))

        return self.result(
            title=music_data.trackName,
            author=self.create_author(music_data.artistName),
            url=share_url,
            contents=contents,
            extra={
                "album": music_data.trackInfo.album.name,
                "lyric": music_data.lyrics,
            },
        )
