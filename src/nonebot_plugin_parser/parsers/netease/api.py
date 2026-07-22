"""网易云音乐 SDK（直连官方公开接口，无需加密、无需外部容器）。

三个接口均来自网易云公开 Web 端，已实测稳定：
- 歌曲详情：GET /api/song/detail/?ids=[{id}] → 标题/歌手/封面URL/时长
- 播放地址：
  - 匿名：GET /song/media/outer/url?id={id}.mp3 → 302 重定向到真实音频地址
    （绕过 weapi/eapi 的 AES+RSA 加密，公开外链直接给真实地址）
  - 登录态：GET /api/song/enhance/player/url?ids=[{id}]&br=320000 → 可拿 VIP 曲目
- 歌词：GET /api/song/lyric?id={id}&lv=1 → LRC 文本

VIP/无版权曲目在匿名「播放地址」接口会被 403/重定向到错误页，本 SDK 据此判定不可播；
传 cookie（含 MUSIC_U）后走 enhance/player/url 接口，VIP 曲目有权限即可返回真实地址。
"""

from typing import TYPE_CHECKING

from nonebot import logger

if TYPE_CHECKING:
    from ..base import BaseParser

_DETAIL_API = "https://music.163.com/api/song/detail/"
_OUTER_URL = "https://music.163.com/song/media/outer/url?id={song_id}.mp3"
# 登录态专用：带 cookie 可解析 VIP/付费曲目（公开 GET 接口，无需 weapi 加密）
_ENHANCE_URL_API = "https://music.163.com/api/song/enhance/player/url"
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


async def get_song_url(parser: "BaseParser", song_id: str, cookie: str | None = None) -> str | None:
    """获取真实音频地址。

    - 有 ``cookie``（含 MUSIC_U 登录态）：走 ``enhance/player/url`` 接口，可解析 VIP 曲目。
    - 无 cookie：走 ``outer/url`` 公开外链 302 重定向，仅免费曲目可播。

    VIP/无版权曲目匿名访问时网易云会 302 到错误页或返回 403，此时返回 None。
    """
    if cookie:
        # 登录态：enhance/player/url 返回 JSON，data[0].url 即真实地址
        resp = await parser.request(
            _ENHANCE_URL_API,
            params={"ids": f"[{song_id}]", "br": 320000},
            headers={**_NETEASE_HEADERS, "Cookie": cookie},
            raise_for_status=False,
        )
        if resp.status_code != 200:
            logger.warning(f"netease enhance/player/url 返回 {resp.status_code}")
            return None
        data = (resp.json() or {}).get("data") or []
        if data and data[0].get("url"):
            return data[0]["url"]
        # 200 但无 url：VIP 无权限或无版权（设计性失败）
        logger.debug(f"netease enhance/player/url data 无 url: code={data[0].get('code') if data else '空'}")
        return None

    # 匿名：outer/url 外链 302 重定向到真实 CDN 地址
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
    logger.debug(f"netease outer/url 未返回音频, status={resp.status_code}, ct={content_type}")
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
