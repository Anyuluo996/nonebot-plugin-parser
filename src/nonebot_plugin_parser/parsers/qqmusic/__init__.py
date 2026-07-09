"""QQ音乐解析器（基于 ``qqmusic-api-python``，开箱即用、无需 Meting 容器）。

支持链接格式：
- 长链 y.qq.com/n/ryqq/songDetail/数字、y.qq.com/n/ryqq/v2/songDetail/数字
- 短链 c*.y.qq.com/base/fcgi-bin/u?__=xxx（需重定向解析）

免费歌曲无需任何配置即可解析；VIP/付费歌曲需先用 ``qqmusic登录`` 扫码登录
（凭证持久化到本地，见 :mod:`parsers.qqmusic.credential`）。
"""

import re
from typing import ClassVar

from . import api as qqmusic_api
from . import credential as qqmusic_credential
from ..base import Platform, BaseParser, PlatformEnum, IgnoreException, handle

_QQ_SONGID_RE = re.compile(r"songDetail/(?:\?songmid=|)(?P<song_id>\w+)")


class QQMusicParser(BaseParser):
    """QQ音乐解析器（直连，无需 Meting）。"""

    platform: ClassVar[Platform] = Platform(name=PlatformEnum.QQMUSIC, display_name="QQ音乐")

    async def _parse_by_song_id(self, song_mid: str, share_url: str):
        """统一解析流程：详情 → 播放地址 → 歌词 + 封面。"""
        detail = await qqmusic_api.get_song_detail(self, song_mid)
        if not detail:
            raise IgnoreException("未找到该歌曲")

        # 匿名可解析免费歌曲；若已扫码登录则能拿到 VIP/付费曲目
        credential = qqmusic_credential.load_credential()
        audio_url = await qqmusic_api.get_play_url(
            self,
            detail["song_mid"],
            detail["media_mid"],
            credential=credential,
        )
        if not audio_url:
            if not credential:
                tip = "（可能 VIP/付费曲目，可用「qqmusic登录」扫码后重试）"
            else:
                tip = "（账号无该曲权限或无版权）"
            raise IgnoreException(f"无法获取音频下载地址{tip}")

        contents = [self.create_audio_content(audio_url, duration=detail["duration"])]

        # 封面走渲染专用字段（cover_image），不入 contents，不会被发送
        cover_image = None
        if detail["pic_url"]:
            cover_image = self.create_cover_image_task(detail["pic_url"])

        lyric = await qqmusic_api.get_lyric(self, song_mid)

        return self.result(
            title=detail["name"],
            author=self.create_author(detail["artist"]),
            url=share_url,
            contents=contents,
            cover_image=cover_image,
            extra={"lyric": lyric},
        )

    @handle(
        "y.qq.com",
        r"y\.qq\.com.*?songDetail/(?:\?songmid=|)(?P<song_id>[a-zA-Z0-9_]+)",
    )
    async def _parse_qqmusic(self, searched: re.Match[str]):
        song_id = searched.group("song_id")
        return await self._parse_by_song_id(song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}")

    @handle(
        # QQ音乐分享卡片的 playsong.html 格式 (i.y.qq.com 域, songmid 在查询参数)。
        # 用 playsong.html 作关键词避免与长链 handler 的 "y.qq.com" 冲突
        # (同 keyword 下后注册的 handler 会覆盖前者, 导致 songDetail 长链被误路由)。
        "playsong.html",
        r"playsong\.html.*?[?&]songmid=(?P<song_id>[a-zA-Z0-9_]+)",
    )
    async def _parse_qqmusic_card(self, searched: re.Match[str]):
        song_id = searched.group("song_id")
        return await self._parse_by_song_id(song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}")

    @handle(
        # 用更具体的 keyword 避免与长链 handler 的 "y.qq.com" 冲突
        # （同 keyword 下后注册的 handler 会覆盖前者，导致长链被误路由到短链分支）
        "fcgi-bin/u",
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
        return await self._parse_by_song_id(song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}")
