"""酷狗音乐解析器。

适配自 parser-lite 的 kugou.py。从分享页提取 hash，经 playInfo 接口取音频，
再经歌词接口取 LRC。
"""

import re
import json
import base64
import contextlib
from typing import ClassVar

from msgspec import Struct, field
from msgspec.json import Decoder

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle


class _PlayInfo(Struct):
    errcode: int
    album_img: str = ""
    bitRate: int = 0
    choricSinger: str = ""
    error: str = ""
    fileName: str = ""
    fileSize: int = 0
    extName: str = ""
    hash: str = ""
    imgUrl: str = ""
    intro: str = ""
    mvhash: str = ""
    pay_type: int = 0
    singerId: int = 0
    singerName: str = ""
    songName: str = ""
    timeLength: int = 0
    url: str = ""


class _Candidates(Struct):
    id: str
    accesskey: str
    singer: str
    song: str
    language: str


class _KrcsSearch(Struct):
    errcode: int
    errmsg: str
    expire: int
    candidates: list[_Candidates] = field(default_factory=list)


class _Lyrics(Struct):
    error_code: int
    info: str
    fmt: str
    content: str
    charset: str
    id: str


class KuGouParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.KUGOU, display_name="酷狗音乐"
    )

    def _extract_hash(self, html_text: str) -> str:
        """从分享页提取歌曲 hash。"""
        if smarty_match := re.search(
            r"var dataFromSmarty\s*=\s*(\[.*?\]),", html_text, re.DOTALL
        ):
            with contextlib.suppress(json.JSONDecodeError):
                smarty_data = json.loads(smarty_match[1])
                if isinstance(smarty_data, list) and smarty_data:
                    return smarty_data[0].get("hash", "").upper()
        return ""

    @handle(
        "kugou.com",
        r"https?://[^\s]*?kugou\.com.*?(?:/(?:share|mixsong)/[a-zA-Z0-9]+\.html|(?:id|chain)=[a-zA-Z0-9]+)",
    )
    async def _parse_kugou_share(self, searched: re.Match[str]):
        share_url = searched.group(0)

        resp = await self.request(share_url)
        html_text = resp.text

        _hash = self._extract_hash(html_text)
        if not _hash:
            raise ParseException(f"未找到歌曲hash: {share_url}")

        resp = await self.request(
            f"https://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash={_hash}"
        )
        playinfo = Decoder(_PlayInfo).decode(resp.content)
        if playinfo.errcode != 0:
            raise ParseException(f"酷狗音乐解析失败: {playinfo.errcode} {playinfo.error}")

        audio_url = playinfo.url
        if not audio_url:
            raise ParseException("未找到音频资源")

        audio_name = f"{playinfo.fileName}.{playinfo.extName}"
        contents = [
            self.create_audio_content(
                audio_url,
                duration=playinfo.timeLength,
                audio_name=audio_name,
            )
        ]

        lyric = ""
        with contextlib.suppress(Exception):
            resp = await self.request(
                f"https://krcs.kugou.com/search?hash={playinfo.hash}"
            )
            krcs = Decoder(_KrcsSearch).decode(resp.content)
            if krcs.errcode == 200 and krcs.candidates:
                resp = await self.request(
                    "https://lyrics.kugou.com/download?ver=1&"
                    f"id={krcs.candidates[0].id}&"
                    f"accesskey={krcs.candidates[0].accesskey}&fmt=lrc"
                )
                lyrics = Decoder(_Lyrics).decode(resp.content)
                if lyrics.error_code == 0:
                    lyric = base64.b64decode(lyrics.content).decode(lyrics.charset)

        if cover_url := playinfo.album_img.format(size=480):
            contents.append(self.create_image_content(cover_url))

        return self.result(
            title=playinfo.songName,
            author=self.create_author(playinfo.singerName),
            url=share_url,
            contents=contents,
            extra={
                "info": f"比特率: {playinfo.bitRate}K",
                "lyric": lyric,
            },
        )
