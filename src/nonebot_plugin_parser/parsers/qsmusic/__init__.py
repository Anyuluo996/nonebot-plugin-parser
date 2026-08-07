"""汽水音乐解析器。

适配自 parser-lite 的 qsmusic，解析字节系汽水音乐分享链接。
从分享页 _ROUTER_DATA 提取歌曲资源与歌词。

支持两种分享链接格式：
- ``qishui.douyin.com/s/xxx/``  旧版短链（app 内分享）
- ``music.douyin.com/qishui/share/track?track_id=...``  新版 track 页
  （抖音短链 ``v.douyin.com/xxx`` 重定向后常见的落点，两种页面结构一致，
  均走 ``loaderData.track_page.audioWithLyricsOption``）
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
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.QSMUSIC, display_name="汽水音乐")

    @handle(
        "qishui.douyin.com",
        r"https?://[^\s]*?qishui\.douyin\.com/s/[a-zA-Z0-9]+/",
    )
    @handle(
        "music.douyin.com",
        # 只锚定到 track_id（页面唯一有效参数），不捕获后续 &from_item_id 等统计参数，
        # 避免尾部标点/空白污染 group(0) 作为 share_url 请求。
        r"https?://music\.douyin\.com/qishui/share/track\?track_id=\d+",
    )
    async def _parse_qsmusic_share(self, searched: re.Match[str]):
        share_url = searched.group(0)

        resp = await self.request(share_url, headers=self.ios_headers)
        html = resp.text
        if matched := _ROUTER_DATA.search(html):
            raw = matched[1]
        else:
            raise ParseException("未找到结构化数据")

        music_data = shareDecoder.decode(raw).loaderData.track_page.audioWithLyricsOption

        contents = [self.create_audio_content(music_data.url, duration=music_data.duration)]

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
