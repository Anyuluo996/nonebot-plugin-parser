import json
from enum import IntEnum

from bs4 import BeautifulSoup
from msgspec import Struct


class PostType(IntEnum):
    DOCUMENT = 1
    PHOTO = 2
    MUSIC = 3


class BlogInfo(Struct):
    blogId: int
    blogName: str
    blogNickName: str
    bigAvaImg: str
    homePageUrl: str


class PostCount(Struct):
    responseCount: int
    favoriteCount: int
    shareCount: int
    reblogCount: int
    postHot: int


class Post(Struct):
    type: PostType
    blogId: int
    title: str
    publishTime: int
    tag: str
    tagList: list[str]
    content: str
    blogInfo: BlogInfo
    postCount: PostCount
    ipLocation: str
    photoLinks: str

    @property
    def text(self) -> str:
        soup = BeautifulSoup(self.content, "html.parser")
        return soup.get_text("\n", strip=True)

    @property
    def photo_urls(self) -> list[str]:
        return [photo["orign"] for photo in json.loads(self.photoLinks) if "orign" in photo]
