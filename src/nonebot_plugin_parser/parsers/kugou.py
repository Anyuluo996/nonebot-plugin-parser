"""酷狗音乐解析器（直连官方接口，无需 Meting 或外部容器）。

支持链接格式：
- 分享链接 ``kugou.com/share/xxx.html``、``kugou.com/mixsong/xxx.html``（重定向提取 hash）
- 直接含 hash 的歌曲页 ``kugou.com/song/#hash=...``、``t.kugou.com/song/?hash=...``

通过重定向/参数提取 song hash 后直连官方接口：
- 详情/播放地址/歌词全部走 :mod:`kugou_api` 的纯 Python 签名调用。

免费歌曲开箱即用；VIP/付费或被 SSA 风控时返回提示。
"""

import re
from typing import ClassVar

from . import kugou_api
from .base import Platform, BaseParser, PlatformEnum, IgnoreException, handle

_HASH_RE = re.compile(r"hash=([a-zA-Z0-9]+)")
# 页面内嵌 JSON 中的 hash 字段（share/song.html?chain= 格式不重定向，hash 在 body 里）
_HASH_JSON_RE = re.compile(r'"hash":"([a-zA-Z0-9]+)"')


class KuGouParser(BaseParser):
    """酷狗音乐解析器（直连，无需 Meting）。"""

    platform: ClassVar[Platform] = Platform(name=PlatformEnum.KUGOU, display_name="酷狗音乐")

    async def _parse_by_hash(self, song_hash: str, share_url: str):
        """统一解析流程（已知 song hash）：详情 → 播放地址 → 歌词 + 封面。

        供 ``_parse_kugou``（链接解析）和点歌选择（``music_order``）复用,
        行为与原内联实现完全一致。
        """
        detail = await kugou_api.get_song_detail(self, song_hash)
        if not detail:
            raise IgnoreException("未找到该歌曲")

        audio_url = await kugou_api.get_play_url(self, song_hash)
        if not audio_url:
            raise IgnoreException("无法获取音频下载地址（可能 VIP/无版权/被风控）")

        lyric = await kugou_api.get_lyric(self, song_hash, duration=detail.get("duration", 0))

        cover_image = None
        if detail.get("pic_url"):
            cover_image = self.create_cover_image_task(detail["pic_url"])

        return self.result(
            title=detail["name"],
            author=self.create_author(detail["author"]),
            url=share_url,
            contents=[self.create_audio_content(audio_url, duration=detail.get("duration", 0))],
            cover_image=cover_image,
            extra={"lyric": lyric},
        )

    @handle(
        "kugou.com",
        # 匹配到 URL 末尾，避免 share/song.html?chain= 被截断在 .html 处
        r"https?://[^\s]*?kugou\.com[^\s]*(?:/(?:share|mixsong)/[a-zA-Z0-9]+\.html|(?:id|chain|hash)=[a-zA-Z0-9]+)[^\s]*",
    )
    async def _parse_kugou(self, searched: re.Match[str]):
        share_url = searched.group(0)
        song_hash = None

        # 优先从 URL 参数直接提取 hash（song/#hash= / t.kugou/song/?hash=）
        if m := _HASH_RE.search(share_url):
            song_hash = m.group(1)

        # share/mixsong/chain 链接需请求页面拿 hash
        if not song_hash:
            resp = await self.request(share_url, follow_redirects=True, raise_for_status=False)
            final_url = str(resp.url)
            if m := _HASH_RE.search(final_url):
                song_hash = m.group(1)
            elif m := _HASH_JSON_RE.search(resp.text):
                # share/song.html?chain= 格式：页面内嵌 JSON 含 hash 字段
                song_hash = m.group(1)
            elif m := re.search(r"songhash=([a-zA-Z0-9]+)", resp.text):
                song_hash = m.group(1)

        if not song_hash:
            raise IgnoreException("无法从酷狗链接提取歌曲 hash")

        return await self._parse_by_hash(song_hash, share_url=share_url)
