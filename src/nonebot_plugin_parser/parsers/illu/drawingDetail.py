from msgspec import Struct
from msgspec.json import Decoder

from .models import Time, User


class DrawingDetail(Struct):
    author: User
    collectCount: int
    commentCount: int
    title: str
    content: str
    images: list  # list[File]
    likeCount: int
    readCount: int
    rewardCoin: int
    objectId: str
    modifyDate: Time
    publishDate: Time


decoder = Decoder(DrawingDetail)
