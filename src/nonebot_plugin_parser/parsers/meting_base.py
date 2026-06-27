"""基于 Meting-API 的音乐平台解析基类。

Meting-API（metowolf/Meting-API）提供统一的多平台音乐接口：
  GET /api?server={平台}&type=song&id={歌曲id}
  → {title, author, url(代理), pic(代理), lrc(代理)}

子类只需：
- 设置 `_meting_server`（netease/tencent/kugou/baidu/kuwo）
- 实现 `_extract_song_id(searched)` 从匹配中提取歌曲 id

音频/歌词/封面获取逻辑全部复用本基类。
"""

import contextlib
from abc import ABC
from typing import ClassVar

from .base import BaseParser, IgnoreException


class MetingBaseParser(BaseParser, ABC):
    """Meting-API 统一音乐解析基类。

    继承 ABC 避免被 __init_subclass__ 当作具体平台注册（基类无 platform 属性）。
    """
    """Meting-API 统一音乐解析基类。子类需覆盖 _meting_server 和 _extract_song_id。"""

    _meting_server: ClassVar[str] = ""

    @staticmethod
    def _api_base() -> str | None:
        from ..config import pconfig

        return pconfig.meting_api

    async def _fetch_song(self, song_id: str) -> dict:
        """通过 meting-api 获取歌曲信息（title/author/url/pic/lrc 代理链接）。"""
        api = self._api_base()
        if not api:
            raise IgnoreException(
                "音乐解析未配置，请在 parser_meting_api 设置 Meting-API 地址"
            )
        resp = await self.request(
            f"{api}/api", params={"server": self._meting_server, "type": "song", "id": song_id}
        )
        data = resp.json()
        if isinstance(data, list):
            if not data:
                raise IgnoreException("未找到该歌曲")
            return data[0]
        return data

    async def _resolve_url(self, proxy_url: str) -> str | None:
        """跟随 meting-api 的 url 代理链接 302 重定向，拿到真实音频地址。"""
        with contextlib.suppress(Exception):
            resp = await self.request(proxy_url, follow_redirects=False, raise_for_status=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                return resp.headers.get("location")
        return None

    async def _fetch_lyric(self, proxy_url: str) -> str:
        """获取歌词文本（meting-api lrc 接口返回纯文本）。"""
        with contextlib.suppress(Exception):
            resp = await self.request(proxy_url)
            if resp.status_code == 200:
                return resp.text
        return ""

    async def _parse_by_song_id(self, song_id: str, share_url: str):
        """统一的解析流程：song 详情 → 真实 url → 歌词。"""
        song = await self._fetch_song(song_id)
        title = song.get("title", "未知歌曲")
        author_name = song.get("author", "未知歌手")

        proxy_url = song.get("url", "")
        audio_url = await self._resolve_url(proxy_url) if proxy_url else None
        if not audio_url:
            raise IgnoreException("无法获取音频下载地址（可能为 VIP/无版权）")

        contents = [self.create_audio_content(audio_url)]

        lyric = ""
        if lrc_proxy := song.get("lrc", ""):
            lyric = await self._fetch_lyric(lrc_proxy)

        return self.result(
            title=title,
            author=self.create_author(author_name),
            url=share_url,
            contents=contents,
            extra={"lyric": lyric},
        )

    async def _parse_meting(self, searched):
        """子类用 @handle 装饰后指向本方法，需覆盖 _extract_song_id。"""
        song_id = self._extract_song_id(searched)
        return await self._parse_by_song_id(song_id, share_url=f"https://{searched.group(0)}")

    def _extract_song_id(self, searched) -> str:
        raise NotImplementedError("子类必须实现 _extract_song_id")
