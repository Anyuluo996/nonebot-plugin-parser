from random import choice

import msgspec
from msgspec import Struct, field
from msgspec.json import Decoder

# === 旧格式: 视频/实况照片 slides (aweme/v1/web/aweme/detail 返回 images[]) ===
# 兼容 slides 类型 (isPicture=false) 的旧 aweme_detail 结构


class PlayAddr(Struct):
    url_list: list[str]


class Cover(Struct):
    url_list: list[str]


class Video(Struct):
    play_addr: PlayAddr
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
    """抖音图文/实况照片(slides)单条数据 (旧 isPicture=false 格式)"""

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

        使用 play_addr: 音频完整(含说话声)、无水印、无抖音片尾;
        download_addr 虽时长更长但会在结尾多拼一段抖音片尾, 且带水印。

        每个 url_list 里优先取官方 play API (/aweme/v1/play/) 形式,
        CDN 镜像 (douyinvod.com) 偶发 403/404, play API 更稳定。
        """
        urls = []
        for img in self.images:
            if not img.video:
                continue
            urls.append(self._prefer_play_api(img.video.play_addr.url_list))
        return urls

    @staticmethod
    def _prefer_play_api(url_list: list[str]) -> str:
        """优先返回官方 play API 形式的 URL, 其次随机选 CDN 镜像。"""
        for u in url_list:
            if "/aweme/v1/play" in u:
                return u
        return choice(url_list)

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

    @property
    def create_time_seconds(self) -> int:
        """PC detail 接口旧格式 createTime 已是秒, 直接返回。"""
        return self.create_time


# === 新格式: isPicture=true 的 picture 类型图文 ===
# 字段是 pictureList[].url + pictureList[].videoBitRateList[].url
# 详见 issue DOuyin_Note_Slides_Decode_Failure 的真实响应 dump。
# 字段名映射用 field(name=...) 显式声明 camelCase JSON 字段。


class PictureVideoBitRate(Struct):
    """picture 类型单张图对应的实况视频码率列表项

    真实字段 (note/7450744229229235491 PC detail 响应):
        cover / url / width / height / bitRate / dataSize / format
        isH265 / fps / gearName / qualityType / backUrl
    """

    cover: str | None = None
    url: str = ""
    width: int = 0
    height: int = 0
    bit_rate: int = field(default=0, name="bitRate")
    data_size: int = field(default=0, name="dataSize")
    format: str = ""
    is_h265: int = field(default=0, name="isH265")
    fps: int = 0
    gear_name: str = field(default="", name="gearName")
    quality_type: int = field(default=0, name="qualityType")
    back_url: list = field(default_factory=list, name="backUrl")


class Picture(Struct):
    """picture 类型单张图

    静态图: 只有 url + width/height
    实况照片: url + videoBitRateList[0] (实况视频 mp4)
    """

    url: str
    width: int = 0
    height: int = 0
    video_bit_rate_list: list[PictureVideoBitRate] = field(
        default_factory=list, name="videoBitRateList"
    )


class PictureSlidesData(Struct):
    """抖音 picture 类型图文 (isPicture=true, 返回 pictureList[])

    与 SlidesData 接口对齐: name / avatar_url / image_urls /
    dynamic_urls / dynamic_cover_urls / create_time_seconds,
    parse_slides 无需关心是哪种格式, 直接调同名 property 即可。

    createTime 来自 API 是**毫秒** (例如 1734761606000),
    通过 create_time_seconds 转换为秒, 对齐 result.timestamp 期望。
    """

    nickname: str
    desc: str
    create_time: int = field(name="createTime")  # 毫秒
    picture_list: list[Picture] = field(default_factory=list, name="pictureList")
    uid: str | int = ""
    aweme_id: str | int = field(default="", name="awemeId")
    is_picture: bool = field(default=True, name="isPicture")

    @property
    def name(self) -> str:
        return self.nickname

    @property
    def avatar_url(self) -> str:
        # 新格式 aweme_detail 顶层不返回 author.avatar_thumb, 渲染层降级显示空头像
        return ""

    @property
    def create_time_seconds(self) -> int:
        """毫秒 -> 秒, 对齐 datetime.fromtimestamp 期望。"""
        return self.create_time // 1000

    @property
    def image_urls(self) -> list[str]:
        # 跳过带 video 的图片(实况照片), 它们由 dynamic_urls 作为视频单独输出
        return [p.url for p in self.picture_list if not p.video_bit_rate_list]

    @property
    def dynamic_urls(self) -> list[str]:
        """实况照片(live photo)对应的视频 URL

        picture 类型 API 的 videoBitRateList[0].url **本身就是**官方
        /aweme/v1/play/ 形式 (如 sample 里的
        https://www.douyin.com/aweme/v1/play/?file_id=...&is_ssr=1
        &source=PackSourceEnum_AWEME_DETAIL), 无需像旧 SlidesData 那样
        在 url_list 里 _prefer_play_api 挑选。
        """
        urls = []
        for p in self.picture_list:
            if p.video_bit_rate_list:
                urls.append(p.video_bit_rate_list[0].url)
        return urls

    @property
    def dynamic_cover_urls(self) -> list[str]:
        """实况照片视频对应的封面 URL

        优先 videoBitRateList[0].cover (单独 live video 封面),
        没有则用 picture.url (静态图) 兜底。
        """
        covers = []
        for p in self.picture_list:
            if p.video_bit_rate_list:
                br = p.video_bit_rate_list[0]
                covers.append(br.cover or p.url)
        return covers


# === 顶层响应结构 + 智能 dispatch ===
#
# msgspec 不支持未打 tag 的 Struct union, 用双 decoder + try/except 兜齐。
# - 旧 slides 格式必带 `author` + `images[]`
# - 新 picture 格式必带 `nickname` + `pictureList[]`
# 互斥无歧义, 旧格式失败自动试新格式。


class AwemeDetailRes(Struct):
    """PC web detail API 顶层 (旧 slides 格式)

    保留类名和 detail_decoder 是为了向后兼容老测试代码。
    """

    aweme_detail: SlidesData | None = None


# PC web detail API 解码器 (旧 slides 格式)
detail_decoder = Decoder(AwemeDetailRes)


class PictureDetailRes(Struct):
    """PC web detail API 顶层 (新 picture 格式)"""

    aweme_detail: PictureSlidesData | None = None


picture_detail_decoder = Decoder(PictureDetailRes)


# 智能 dispatch: 旧格式优先, 失败回退新格式
def decode_aweme_detail(
    raw: bytes,
) -> SlidesData | PictureSlidesData | None:
    """PC detail API 顶层响应解码, 自动选旧/新格式。

    Returns:
        解码后的 aweme_detail (SlidesData 或 PictureSlidesData) 或 None
    Raises:
        msgspec.ValidationError: 两种格式都不匹配时抛出原错误
    """
    try:
        aweme = detail_decoder.decode(raw).aweme_detail
        if aweme is not None:
            return aweme
    except msgspec.ValidationError:
        pass
    # 新格式
    return picture_detail_decoder.decode(raw).aweme_detail


# 顶层结构(兼容旧的 slidesinfo v2 API): {"aweme_details": [...]}
class SlidesInfo(Struct):
    aweme_details: list[SlidesData] = field(default_factory=list)


# 旧 slidesinfo v2 API 解码器
decoder = Decoder(SlidesInfo)
