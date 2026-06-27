from typing import Any

from bs4 import BeautifulSoup
from msgspec import Struct, field
from bs4.element import Tag, NavigableString

from .share import ShareData


class News(Struct):
    author: str
    user_id: str
    avatar: str
    body: str
    ip_location: str
    publish_time: int
    replies: int
    title: str
    ups_num: int
    views: int
    share_data: ShareData

    def to_graphics(self, create_image) -> list[Any]:
        """HTML → graphics。"""
        data: list[Any] = []
        soup = BeautifulSoup(self.body, "html.parser")
        for element in soup.descendants:
            if isinstance(element, Tag):
                if element.name == "div" and "video-content" in (element.get("class") or []):
                    element.decompose()
                    continue
                if element.name == "img":
                    if src := element.attrs.get("data-original"):
                        data.append(create_image(str(src)))
            elif isinstance(element, NavigableString):
                if text := str(element).strip():
                    data.append(text)
        return data


class GalleryUser(Struct):
    user_id: str
    nickname: str
    avatar: str
    ip_location: str


class GalleryPreview(Struct):
    share_data: ShareData
    description: str
    icon_url: str
    publish_time: int
    ups_num: int
    user_id: str


class Gallery(Struct):
    preview: GalleryPreview
    user_infos: dict[str, GalleryUser] = field(default_factory=dict)
