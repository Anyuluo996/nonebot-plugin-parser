from msgspec import Struct, field
from msgspec.json import Decoder


class FeedData(Struct):
    title: str
    username: str
    userAvatar: str
    dateline: int | None = field(default=None)
    message: str = field(default="")
    picArr: list[str] | None = field(default=None)


class PageProps(Struct):
    feed: FeedData
    id: str
    aiSummary: str | None = field(default=None)


class Props(Struct):
    pageProps: PageProps


class Feed(Struct):
    props: Props


decoder = Decoder(Feed)
