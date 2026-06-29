"""网易云音乐 SDK（直连官方公开接口，无需加密、无需登录、无需外部容器）。

三个接口均来自网易云公开 Web 端，已实测稳定：
- 歌曲详情：GET /api/song/detail/?ids=[{id}] → 标题/歌手/封面URL/时长
- 播放地址：GET /song/media/outer/url?id={id}.mp3 → 302 重定向到真实音频地址
  （这是关键：绕过了 weapi/eapi 的 AES+RSA 加密，公开外链直接给真实地址）
- 歌词：GET /api/song/lyric?id={id}&lv=1 → LRC 文本

VIP/无版权曲目在「播放地址」接口会被 403/重定向到错误页，本 SDK 据此判定不可播。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..base import BaseParser

_DETAIL_API = "https://music.163.com/api/song/detail/"
_OUTER_URL = "https://music.163.com/song/media/outer/url?id={song_id}.mp3"
_LYRIC_API = "https://music.163.com/api/song/lyric"

# 网易云请求需要的头（Referer 防盗链）
_NETEASE_HEADERS = {
    "Referer": "https://music.163.com/",
}


async def get_song_detail(parser: "BaseParser", song_id: str) -> dict:
    """获取歌曲详情：标题、歌手、封面URL、时长（秒）。

    返回 ``{name, artist, pic_url, duration}``，找不到返回空 dict。
    """
    resp = await parser.request(
        _DETAIL_API,
        params={"ids": f"[{song_id}]"},
        headers=_NETEASE_HEADERS,
    )
    data = resp.json()
    songs = data.get("songs") or []
    if not songs:
        return {}
    s = songs[0]
    artists = s.get("artists") or s.get("singer") or []
    return {
        "name": s.get("name", ""),
        "artist": " / ".join(a.get("name", "") for a in artists) or "未知歌手",
        "pic_url": (s.get("album") or {}).get("picUrl", "") or "",
        "duration": (s.get("duration") or 0) / 1000.0,  # ms → s
    }


async def get_song_url(parser: "BaseParser", song_id: str) -> str | None:
    """获取真实音频地址。

    通过 ``/song/media/outer/url`` 外链接口，跟随重定向拿到最终 CDN 地址。
    若歌曲 VIP/无版权，网易云会 302 到错误页或返回 403，此时返回 None。
    """
    url = _OUTER_URL.format(song_id=song_id)
    resp = await parser.request(
        url,
        headers=_NETEASE_HEADERS,
        follow_redirects=True,
        raise_for_status=False,
    )
    # 真实音频: content-type 为 audio/*
    content_type = resp.headers.get("content-type", "")
    if resp.status_code == 200 and "audio" in content_type:
        return str(resp.url)
    # 有些情况网易云返回 200 但内容是 html 错误页（VIP 提示），content-type 不是 audio
    return None


async def get_lyric(parser: "BaseParser", song_id: str) -> str:
    """获取歌词（LRC 文本）。无歌词返回空串。"""
    resp = await parser.request(
        _LYRIC_API,
        params={"id": song_id, "lv": 1, "kv": 1, "tv": -1},
        headers=_NETEASE_HEADERS,
        raise_for_status=False,
    )
    if resp.status_code != 200:
        return ""
    data = resp.json()
    return (data.get("lrc") or {}).get("lyric", "") or ""
