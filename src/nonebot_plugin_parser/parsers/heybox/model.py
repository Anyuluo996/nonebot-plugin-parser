"""小黑盒数据模型。

适配自 parser-lite 的 heybox/model.py。
简化：表情占位符 [xxx] 剥离为纯文本（不下载表情图），保留图文/视频核心内容。
"""

import json

from bs4 import BeautifulSoup
from msgspec import Struct, field
from bs4.element import Tag, NavigableString


def _optimize_image_url(url: str) -> str:
    """小黑盒 CDN 特性：带查询参数(?)的 URL 末尾加 \\ 才能取到原图。
    不带 ? 的直接返回（参考 zhiyu1998/rconsole-plugin optimizeImageUrl）。"""
    url = url.rstrip("\\")
    return url + "\\" if "?" in url else url


class User(Struct):
    avatar: str
    username: str
    userid: str | int

    @property
    def avatar_url(self) -> str:
        return _optimize_image_url(self.avatar)


class Link(Struct):
    has_video: int
    title: str
    description: str
    text: str
    ip_location: str
    click: int
    comment_num: int
    create_at: int
    favour_count: int
    link_award_num: int
    forward_num: int
    user: User
    video_url: str | None = None
    video_thumb: str | None = None

    def to_graphics(self, create_image, create_video=None) -> list:
        """格式化正文为 graphics（文本 + 图片 + 视频）。"""
        content: list = []
        try:
            parts = json.loads(self.text)
            for part in parts:
                if part["type"] == "html":
                    content.extend(self._extract_from_html(part["text"], create_image))
                    break
                if part["type"] == "text":
                    content.append(part["text"])
                elif part["type"] == "img":
                    content.append(create_image(_optimize_image_url(part["url"])))
        except (json.JSONDecodeError, TypeError):
            if self.text:
                content.append(self.text)
        if self.has_video and self.video_url and self.video_thumb and create_video:
            content.append(create_video(self.video_url, cover_url=self.video_thumb))
        return content

    @staticmethod
    def _extract_from_html(html: str, create_image) -> list:
        soup = BeautifulSoup(html.replace(r"\"", '"'), "html.parser")
        for noscript in soup.find_all("noscript"):
            noscript.decompose()
        result: list = []
        for element in soup.descendants:
            if isinstance(element, Tag) and element.name == "img":
                attrs = element.attrs or {}
                src = (
                    attrs.get("data-original") or attrs.get("data-actualsrc") or attrs.get("data-default-watermark-src")
                )
                if src:
                    result.append(create_image(str(src)))
            elif isinstance(element, NavigableString):
                if text := str(element).strip():
                    result.append(text)
        return result


class BaseResult(Struct):
    link: Link
    comments: list = field(default_factory=list)
