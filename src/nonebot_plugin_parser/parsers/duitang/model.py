from msgspec import Struct


class Sender(Struct):
    id: int
    username: str
    avatar: str


class Photo(Struct):
    path: str


class Blog(Struct):
    photo: Photo
    short_video: bool
    copyright_author_name: str


class BlogData(Blog):
    msg: str
    id: int
    sender: Sender
    reply_count: int
    add_datetime_ts: int
    like_count: int
    favorite_count: int
    atlas_id: int


class AtlasData(Struct):
    id: int
    desc: str
    blogs: list[Blog]
    favorite_count: int
    like_count: int
    comment_count: int
    visit_count: int
    created_at: int
    sender: Sender

    @property
    def img_list(self) -> list[str]:
        return [blog.photo.path for blog in self.blogs]
