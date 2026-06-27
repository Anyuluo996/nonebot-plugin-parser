"""知乎正文富内容解析：HTML → 有序的 文本行 / 图片 / 视频列表（graphics）。

适配自 parser-lite 的 zhihu/util.py，将其 Creator 工厂替换为本项目的
BaseParser.create_image_content / create_video_content，产出格式对齐
graphics: list[str | ImageContent]（同 NGA 多楼层渲染）。
"""

from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup
from bs4.element import Tag, NavigableString

from ...download import DOWNLOADER

if TYPE_CHECKING:
    from ..base import BaseParser

VIDEO_HEADER = {**DOWNLOADER.headers, "x-app-za": "OS=webplayer", "x-referer": ""}


def _quality_rank(q: str) -> int:
    """把 'FHD'/'HD'/'SD' 映射到数值，越大越好。"""
    q = q.upper()
    if q == "FHD":
        return 3
    if q == "HD":
        return 2
    return 1 if q == "SD" else 0


async def _fetch_video(parser: "BaseParser", video_id: str, content_type: str) -> Any:
    res = await parser.request(
        "https://www.zhihu.com/api/v4/video/play_info",
        method="POST",
        headers=VIDEO_HEADER,
        json={
            "content_id": video_id,
            "video_id": video_id,
            "content_type_str": content_type,
            "is_only_video": True,
            "scene_code": "answer_detail_web",
        },
    )
    data = res.json()
    video_play = data["video_play"]
    mp4_list = video_play.get("playlist", {}).get("mp4")
    if not mp4_list:
        return None

    best_item = max(mp4_list, key=lambda item: _quality_rank(item["quality"]))
    return parser.create_video_content(
        best_item["url"][0],
        cover_url=video_play["default_cover"],
        duration=best_item["duration"],
    )


async def parse_rich_content(
    parser: "BaseParser", html: str, content_type: str
) -> list[str | Any]:
    """将知乎内容 HTML 解析为有顺序的 文本行 + 图片/视频 列表（graphics 格式）。"""
    soup = BeautifulSoup(html.replace(r"\"", '"'), "html.parser")
    _clean_soup(soup)

    result: list[str | Any] = []
    buffer: list[str] = []

    async for item in _iter_media_and_text(parser, soup, content_type):
        if isinstance(item, str):
            buffer.append(item)
        else:
            if buffer:
                text_block = "".join(buffer)
                lines = [line.rstrip() for line in text_block.splitlines()]
                if normalized := "\n".join(lines).strip():
                    result.append(normalized)
                buffer.clear()
            result.append(item)

    if buffer:
        text_block = "".join(buffer)
        lines = [line.rstrip() for line in text_block.splitlines()]
        if normalized := "\n".join(lines).strip():
            result.append(normalized)

    return result


def _clean_soup(soup: BeautifulSoup) -> None:
    """预清洗 DOM：移除 noscript 等无效节点。"""
    for noscript in soup.find_all("noscript"):
        noscript.decompose()


async def _iter_media_and_text(
    parser: "BaseParser", soup: BeautifulSoup, content_type: str
):
    """按 DOM 顺序依次产出文本 / 图片 / 视频等内容。"""
    for element in soup.descendants:
        if isinstance(element, Tag):
            if element.name == "p":
                yield "\n"
                continue

            if element.name == "br":
                yield "\n"
                continue

            if element.name == "a" and "video-box" in (element.get("class") or []):
                video = await _parse_video_box(parser, element, content_type)
                if video:
                    yield video

                if data_name := element.get("data-name"):
                    if text := str(data_name).strip():
                        yield text

                element.decompose()
                continue

            if element.name == "img":
                attrs: dict[str, str] = {
                    str(k): str(v[0] if isinstance(v, list) and v else v)
                    for k, v in (element.attrs or {}).items()
                    if v
                }
                if src := (
                    attrs.get("data-original")
                    or attrs.get("data-actualsrc")
                    or attrs.get("data-default-watermark-src")
                    or attrs.get("src")
                ):
                    yield parser.create_image_content(src)

        elif isinstance(element, NavigableString):
            text = str(element)
            if text.strip():
                yield text


async def _parse_video_box(parser: "BaseParser", tag: Tag, content_type: str):
    """解析知乎 <a class="video-box">，根据 data-lens-id 拉取视频信息"""
    video_id = tag.get("data-lens-id")
    if not isinstance(video_id, str) or not video_id:
        return None
    return await _fetch_video(parser, video_id, content_type) if video_id else None
