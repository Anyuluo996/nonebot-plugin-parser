"""酷我音乐解析器。

适配自 parser-lite 的 kuwo.py，使用第三方 API kw-api.cenguigui.cn。
依赖第三方服务可用性。
"""

import re
from typing import ClassVar

from .base import Platform, BaseParser, PlatformEnum, ParseException, handle


def _display_duration(duration: int) -> str:
    try:
        if duration <= 0:
            return "0:00"
        minutes, seconds = divmod(duration, 60)
        if minutes < 60:
            return f"{minutes}:{seconds:02d}"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    except (TypeError, ValueError):
        return "NaN"


class KuWoParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.KUWO, display_name="酷我音乐"
    )

    @handle("kuwo.cn", r"kuwo\.cn/play_detail/(?P<rid>\d+)")
    async def _parse_kuwo(self, searched: re.Match[str]):
        rid = searched.group("rid")

        resp = await self.request(
            "https://kw-api.cenguigui.cn/",
            params={"id": rid, "type": "song", "level": "exhigh", "format": "json"},
        )
        data = resp.json()
        if data["code"] != 200:
            raise ParseException(
                f"酷我音乐接口返回错误: {data.get('msg', '未知错误')}"
            )
        music_data = data["data"]
        audio_url = music_data["url"]
        if not audio_url.startswith("http"):
            raise ParseException("无效音乐URL")
        duration = music_data["duration"]

        contents = [self.create_audio_content(audio_url, duration)]

        return self.result(
            title=music_data["name"],
            author=self.create_author(music_data["artist"]),
            url=f"https://www.kuwo.cn/play_detail/{rid}",
            contents=contents,
            extra={
                "album": music_data.get("album"),
                "info": f"时长: {_display_duration(duration)}",
                "lyric": music_data.get("lyric"),
            },
        )
