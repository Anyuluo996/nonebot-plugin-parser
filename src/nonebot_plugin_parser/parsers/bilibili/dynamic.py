from typing import Any

from msgspec import Struct, convert


class AuthorInfo(Struct):
    """作者信息"""

    name: str
    face: str
    mid: int
    pub_time: str
    pub_ts: int


class VideoArchive(Struct):
    """视频信息"""

    aid: str
    bvid: str
    title: str
    desc: str
    cover: str


class OpusImage(Struct):
    """图文动态图片信息"""

    url: str


class OpusSummary(Struct):
    """图文动态摘要"""

    text: str


class OpusContent(Struct):
    """图文动态内容"""

    jump_url: str
    pics: list[OpusImage]
    summary: OpusSummary
    title: str | None = None


class DrawItem(Struct):
    """普通图文动态图片项"""

    src: str
    width: int
    height: int


class Draw(Struct):
    """普通图文动态内容"""

    id: int
    items: list[DrawItem]


class DynamicMajor(Struct):
    """动态主要内容"""

    type: str
    archive: VideoArchive | None = None
    opus: OpusContent | None = None
    draw: Draw | None = None  # 新增：普通图文支持

    @property
    def title(self) -> str | None:
        """获取标题"""
        if self.type == "MAJOR_TYPE_ARCHIVE" and self.archive:
            return self.archive.title
        # draw 类型通常没有单独的标题，opus 有
        return None

    @property
    def text(self) -> str | None:
        """获取文本内容"""
        if self.type == "MAJOR_TYPE_ARCHIVE" and self.archive:
            return self.archive.desc
        elif self.type == "MAJOR_TYPE_OPUS" and self.opus:
            return self.opus.summary.text
        # MAJOR_TYPE_DRAW 的文本通常在 desc 字段中，不在这里
        return None

    @property
    def image_urls(self) -> list[str]:
        """获取图片URL列表"""
        if self.type == "MAJOR_TYPE_OPUS" and self.opus:
            return [pic.url for pic in self.opus.pics]
        elif self.type == "MAJOR_TYPE_ARCHIVE" and self.archive and self.archive.cover:
            return [self.archive.cover]
        elif self.type == "MAJOR_TYPE_DRAW" and self.draw:
            # 新增：处理普通图文动态的图片
            return [item.src for item in self.draw.items]
        return []

    @property
    def cover_url(self) -> str | None:
        """获取封面URL"""
        if self.type == "MAJOR_TYPE_ARCHIVE" and self.archive:
            return self.archive.cover
        # 如果是图文，取第一张图作为封面
        images = self.image_urls
        if images:
            return images[0]
        return None


class DynamicModule(Struct):
    """动态模块"""

    module_author: AuthorInfo
    module_dynamic: dict[str, Any] | None = None
    module_stat: dict[str, Any] | None = None

    @property
    def author_name(self) -> str:
        """获取作者名称"""
        return self.module_author.name

    @property
    def author_face(self) -> str:
        """获取作者头像URL"""
        return self.module_author.face

    @property
    def pub_ts(self) -> int:
        """获取发布时间戳"""
        return self.module_author.pub_ts

    @property
    def major_info(self) -> dict[str, Any] | None:
        """获取主要内容信息"""
        if self.module_dynamic:
            return self.module_dynamic.get("major")
        return None

    @property
    def desc_text(self) -> str | None:
        """获取描述文本（转发评论 或 普通动态正文）"""
        if self.module_dynamic:
            desc = self.module_dynamic.get("desc")
            if desc:
                return desc.get("text")
        return None


class DynamicInfo(Struct):
    """动态信息"""

    id_str: str
    type: str
    visible: bool
    modules: DynamicModule
    basic: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        """获取作者名称"""
        return self.modules.author_name

    @property
    def avatar(self) -> str:
        """获取作者头像URL"""
        return self.modules.author_face

    @property
    def timestamp(self) -> int:
        """获取发布时间戳"""
        return self.modules.pub_ts

    @property
    def desc_text(self) -> str | None:
        """获取描述文本（转发评论 或 普通动态正文）"""
        return self.modules.desc_text

    @property
    def title(self) -> str | None:
        """获取标题"""
        major_info = self.modules.major_info
        if major_info:
            major = convert(major_info, DynamicMajor)
            return major.title
        return None

    @property
    def text(self) -> str | None:
        """获取主要内容文本"""
        # 优先尝试获取 Major 中的文本 (Opus/Archive)
        major_info = self.modules.major_info
        if major_info:
            major = convert(major_info, DynamicMajor)
            if t := major.text:
                return t

        # 如果 Major 中没有文本（例如 MAJOR_TYPE_DRAW 或 MAJOR_TYPE_NONE），则返回 desc_text
        return self.desc_text

    @property
    def image_urls(self) -> list[str]:
        """获取图片URL列表"""
        major_info = self.modules.major_info
        if major_info:
            major = convert(major_info, DynamicMajor)
            return major.image_urls
        return []

    @property
    def cover_url(self) -> str | None:
        """获取封面URL"""
        major_info = self.modules.major_info
        if major_info:
            major = convert(major_info, DynamicMajor)
            return major.cover_url
        return None


class DynamicData(Struct):
    """动态项目"""

    item: DynamicInfo
    orig: DynamicInfo | None = None  # 转发的原动态
