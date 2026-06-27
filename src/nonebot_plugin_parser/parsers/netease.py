"""网易云音乐解析器。

使用本地部署的 NeteaseCloudMusicApi（Binaryify）服务。
支持链接格式：
- 长链 music.163.com/#/song?id=X、y.music.163.com/m/song?id=X
- 短链 163cn.tv/xxxxxx（需重定向解析）

需在配置 parser_ncm_api 中指定 API 地址，否则提示未配置。
"""

import re
import contextlib
from typing import ClassVar

from .base import (
    Platform,
    BaseParser,
    PlatformEnum,
    ParseException,
    IgnoreException,
    handle,
)


def _parse_duration_to_seconds(duration_ms: int | None) -> int:
    """毫秒 → 秒。"""
    if not duration_ms or duration_ms <= 0:
        return 0
    return duration_ms // 1000


class NCMParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.NETEASE, display_name="网易云音乐"
    )

    @handle(
        "music.163.com",
        r"music\.163\.com.*?(?:/song(?:\?|/)|[?&]id=)(?P<song_id>\d+)",
    )
    async def _parse_netease(self, searched: re.Match[str]):
        song_id = searched.group("song_id")

        from ..config import pconfig

        api = pconfig.ncm_api
        if not api:
            raise IgnoreException(
                "网易云解析未配置，请在 parser_ncm_api 设置 NeteaseCloudMusicApi 地址"
            )

        # 1. 歌曲详情：标题、歌手、专辑、封面、时长
        resp = await self.request(
            f"{api}/song/detail", params={"ids": song_id}
        )
        detail = resp.json()
        if detail.get("code") != 200 or not detail.get("songs"):
            raise ParseException(f"网易云歌曲详情获取失败: {detail.get('code')}")
        song = detail["songs"][0]
        title = song.get("name", "未知歌曲")
        artists = [a["name"] for a in song.get("ar", []) if a.get("name")]
        artist_name = "、".join(artists) if artists else "未知歌手"
        album = (song.get("al") or {}).get("name", "")
        cover_url = (song.get("al") or {}).get("picUrl", "")
        duration = _parse_duration_to_seconds(song.get("dt"))

        # 2. 音乐 url：下载地址
        resp = await self.request(
            f"{api}/song/url", params={"id": song_id, "br": 320000}
        )
        url_data = resp.json()
        if url_data.get("code") != 200:
            raise ParseException(f"网易云音乐 url 获取失败: {url_data.get('code')}")
        url_list = url_data.get("data", [])
        if not url_list or not url_list[0].get("url"):
            raise ParseException("无法获取音频下载地址（可能为 VIP/无版权）")
        audio_url = url_list[0]["url"]
        audio_type = url_list[0].get("type", "mp3") or "mp3"
        audio_name = f"{title}-{artist_name}.{audio_type}"

        contents = [
            self.create_audio_content(
                audio_url, duration=duration, audio_name=audio_name
            )
        ]
        if cover_url:
            contents.append(self.create_image_content(cover_url))

        # 3. 歌词
        lyric = ""
        with contextlib.suppress(Exception):
            resp = await self.request(f"{api}/lyric", params={"id": song_id})
            lyric_data = resp.json()
            if lyric_data.get("code") == 200:
                lyric = (lyric_data.get("lrc") or {}).get("lyric", "")

        info_parts = [f"格式: {audio_type}"]
        if album:
            info_parts.append(f"专辑: {album}")
        return self.result(
            title=title,
            author=self.create_author(artist_name),
            url=f"https://music.163.com/song/{song_id}",
            contents=contents,
            extra={"info": " | ".join(info_parts), "lyric": lyric},
        )

    @handle("163cn.tv", r"https?://[^\s]*?163cn\.tv/(?P<code>[a-zA-Z0-9]+)")
    async def _parse_163cn_short(self, searched: re.Match[str]):
        """网易云短链 163cn.tv/xxxx，跟随重定向拿到真实 song 链接再解析。"""
        short_url = searched.group(0)
        resp = await self.request(
            short_url, follow_redirects=True, raise_for_status=False
        )
        final_url = str(resp.url)
        # 从重定向后的 URL 里提取 song id
        if m := re.search(r"song(?:\?|/|_)[^\d]*(\d+)", final_url):
            song_id = m.group(1)
        elif m := re.search(r"[?&]id=(\d+)", final_url):
            song_id = m.group(1)
        else:
            raise ParseException(f"无法从短链解析歌曲 id: {final_url}")
        # 复用长链解析逻辑
        fake_match = re.search(
            r"music\.163\.com.*?(?:/song(?:\?|/)|[?&]id=)(?P<song_id>\d+)",
            f"music.163.com/song?id={song_id}",
        )
        return await self._parse_netease(fake_match)
