from random import choice

from msgspec import Struct, field
from msgspec.json import Decoder


class PlayAddr(Struct):
    url_list: list[str]


class Cover(Struct):
    url_list: list[str]


class Video(Struct):
    play_addr: PlayAddr
    download_addr: PlayAddr | None = None
    cover: Cover | None = None
    duration: int = 0


class Image(Struct):
    url_list: list[str] = field(default_factory=list)
    video: Video | None = None


class Avatar(Struct):
    url_list: list[str]


class Author(Struct):
    nickname: str
    # avatar_larger: Avatar
    avatar_thumb: Avatar


class SlidesData(Struct):
    """抖音图文/实况照片(slides)单条数据"""

    author: Author
    desc: str
    create_time: int
    images: list[Image]

    @property
    def name(self) -> str:
        return self.author.nickname

    @property
    def avatar_url(self) -> str:
        return choice(self.author.avatar_thumb.url_list)

    @property
    def image_urls(self) -> list[str]:
        # 跳过带 video 的图片(实况照片), 它们由 dynamic_urls 作为视频单独输出
        return [choice(img.url_list) for img in self.images if not img.video]

    @property
    def dynamic_urls(self) -> list[str]:
        """实况照片(live photo)对应的视频 URL

        优先使用 download_addr (完整时长, 含原始音频如说话声),
        回退到 play_addr (多为缩短的预览流)。
        """
        urls = []
        for img in self.images:
            if not img.video:
                continue
            # download_addr 是完整时长版本(含说话等原始音频), 但带水印;
            # play_addr 是精简预览流(通常截断、音频可能是 BGM 片段)
            addr = img.video.download_addr or img.video.play_addr
            urls.append(choice(addr.url_list))
        return urls

    @property
    def dynamic_cover_urls(self) -> list[str]:
        """实况照片视频对应的封面 URL"""
        covers = []
        for img in self.images:
            if not img.video:
                continue
            if img.video.cover:
                covers.append(choice(img.video.cover.url_list))
            else:
                covers.append(choice(img.url_list))
        return covers


# PC web detail API 顶层结构: {"aweme_detail": {...}}
class AwemeDetailRes(Struct):
    aweme_detail: SlidesData | None = None


# 顶层结构(兼容旧的 slidesinfo v2 API): {"aweme_details": [...]}
class SlidesInfo(Struct):
    aweme_details: list[SlidesData] = field(default_factory=list)


# PC web detail API 解码器
detail_decoder = Decoder(AwemeDetailRes)
# 旧 slidesinfo v2 API 解码器
decoder = Decoder(SlidesInfo)
