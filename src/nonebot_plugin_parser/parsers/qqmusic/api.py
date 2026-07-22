"""QQ音乐 SDK（基于 ``qqmusic-api-python`` 封装，开箱即用、无需外部容器）。

对外暴露三个纯 ``dict``/``str`` 接口，不把 ``qqmusic_api`` 的模型类型泄漏给上层 parser：

- 歌曲详情：``client.song.get_detail(song_mid)``（固定 Web 平台，匿名可用）
  → 标题/歌手/封面URL/时长/``media_mid``
- 播放地址：``client.song.get_cdn_dispatch()`` + ``client.song.get_song_urls(...)``
  → 拼接 ``{cdn}{purl}``；免费歌曲匿名可得，VIP/付费歌曲需登录态（``credential``）
- 歌词：``client.lyric.get_lyric(song_mid)`` → LRC 文本

匿名（无 ``credential``）时仅能拿到免费歌曲；VIP/付费曲目 ``get_song_urls`` 返回
``result=104003``、无 ``purl``，由上层据此判定不可播。登录态由
``parsers.qqmusic.credential`` 持久化，扫码登录后即可解析 VIP。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nonebot import logger

if TYPE_CHECKING:
    from qqmusic_api import Credential

    from ..base import BaseParser

# 试听音质优先级：从高到低回退，匿名状态下优先取能拿到的免费/试听片段。
# MP3_320/MP3_128 为常见免费音质；ACC_96 对部分 VIP 歌曲也能放出试听级片段。
from qqmusic_api.modules.song import SongFileType

_QUALITY_FALLBACK: tuple[SongFileType, ...] = (
    SongFileType.MP3_320,
    SongFileType.MP3_128,
    SongFileType.ACC_96,
)

# 播放地址接口重试次数与退避间隔，缓解 QQ 音乐服务端限流导致的偶发失败。
_URL_RETRY = 3
_URL_RETRY_INTERVAL = 1.0


async def get_song_detail(parser: "BaseParser", song_mid: str) -> dict:
    """获取歌曲详情：标题、歌手、封面URL、时长（秒）、``media_mid``。

    使用 ``get_detail``（固定 Web 平台），无需登录态即可拿到完整信息。
    找不到歌曲返回空 dict。
    """
    from qqmusic_api import Client

    async with Client() as client:
        resp = await client.song.get_detail(song_mid)
        track = resp.track
        if track is None:
            return {}
        singers = [s.name for s in (track.singer or []) if s.name]
        return {
            "name": track.name or track.title or "",
            "artist": " / ".join(singers) or "未知歌手",
            "pic_url": track.cover_url(500) or "",
            "duration": float(track.interval or 0),  # 秒
            "song_mid": track.mid or song_mid,
            "media_mid": (track.file.media_mid if track.file else "") or "",
        }


async def get_play_url(
    parser: "BaseParser",
    song_mid: str,
    media_mid: str,
    credential: "Credential | None" = None,
) -> str | None:
    """获取真实音频地址。

    依次尝试 ``_QUALITY_FALLBACK`` 中的音质，返回首个 ``result==0`` 且有 ``purl`` 的
    拼接地址（``{cdn}{purl}``）。免费歌曲匿名可得；VIP/付费曲目需传入 ``credential``
    （来自扫码登录），否则所有音质均返回 ``104003``、返回 None。

    QQ 音乐播放地址接口存在服务端限流：免费曲目在高频/连续请求时也可能误报
    ``CredentialExpiredError``。因此对整体流程做 ``_URL_RETRY`` 次重试，降低偶发失败。
    """
    import asyncio

    from qqmusic_api import Client
    from qqmusic_api.modules.song import SongFileInfo

    for _ in range(_URL_RETRY):
        async with Client(credential=credential) as client:
            cdn_dispatch = await client.song.get_cdn_dispatch()
            cdn = cdn_dispatch.sip[0] if cdn_dispatch.sip else ""
            if not cdn:
                logger.warning("QQ音乐 get_cdn_dispatch 无可用 CDN")
                return None

            # 每个音质单独 try：命中可放音质即返回，全部失败/异常则回退到下一音质。
            for file_type in _QUALITY_FALLBACK:
                try:
                    urls = await client.song.get_song_urls(
                        [SongFileInfo(mid=song_mid, media_mid=media_mid, file_type=file_type)],
                        credential=credential,
                    )
                except Exception as e:
                    logger.debug(f"QQ音乐 get_song_urls({file_type}) 异常: {e!r}")
                    continue
                for item in urls.data:
                    if item.result == 0 and item.purl:
                        return f"{cdn}{item.purl}"
        # 本轮所有音质都未命中（限流/无权限），短暂退避后重试
        await asyncio.sleep(_URL_RETRY_INTERVAL)
    return None


async def get_lyric(parser: "BaseParser", song_mid: str) -> str:
    """获取歌词（LRC 文本）。无歌词或失败返回空串。"""
    from qqmusic_api import Client

    try:
        async with Client() as client:
            resp = await client.lyric.get_lyric(song_mid)
            return resp.lyric or ""
    except Exception as e:
        logger.warning(f"QQ音乐歌词获取失败: {e!r}")
        return ""
