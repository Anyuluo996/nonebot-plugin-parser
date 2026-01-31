from typing import Any

from msgspec import Struct, convert


class AuthorInfo(Struct):
    """作者信息"""

    name: str
    face: str
    mid: int
    pub_time: str = ""
    pub_ts: int = 0


class VideoArchive(Struct):
    """视频信息"""

    aid: str
    bvid: str
    title: str
    cover: str
    desc: str = ""


class OpusImage(Struct):
    """图文动态图片信息"""

    url: str


class OpusSummary(Struct):
    """图文动态摘要"""

    text: str = ""


class OpusContent(Struct):
    """图文动态内容"""

    pics: list[OpusImage] = []
    # 以下字段在部分动态中可能不存在，设为可选
    jump_url: str | None = None
    summary: OpusSummary | None = None
    title: str | None = None


class DrawItem(Struct):
    """普通图文动态图片项"""

    src: str
    # 宽高设为可选，防止接口不返回导致解析失败
    width: int = 0
    height: int = 0
    size: float = 0.0


class Draw(Struct):
    """普通图文动态内容"""

    id: int
    items: list[DrawItem]


class DynamicMajor(Struct):
    """动态主要内容"""

    type: str
    archive: VideoArchive | None = None
    opus: OpusContent | None = None
    draw: Draw | None = None  # 支持普通图文动态

    @property
    def title(self) -> str | None:
        """获取标题"""
        if self.type == "MAJOR_TYPE_ARCHIVE" and self.archive:
            return self.archive.title
        # OPUS 类型有时有标题
        if self.type == "MAJOR_TYPE_OPUS" and self.opus:
            return self.opus.title
        return None

    @property
    def text(self) -> str | None:
        """获取文本内容"""
        if self.type == "MAJOR_TYPE_ARCHIVE" and self.archive:
            return self.archive.desc
        elif self.type == "MAJOR_TYPE_OPUS" and self.opus and self.opus.summary:
            return self.opus.summary.text
        return None

    @property
    def image_urls(self) -> list[str]:
        """获取图片URL列表"""
        if self.type == "MAJOR_TYPE_OPUS" and self.opus:
            return [pic.url for pic in self.opus.pics]
        elif self.type == "MAJOR_TYPE_ARCHIVE" and self.archive and self.archive.cover:
            return [self.archive.cover]
        elif self.type == "MAJOR_TYPE_DRAW" and self.draw:
            return [item.src for item in self.draw.items]
        return []

    @property
    def cover_url(self) -> str | None:
        """获取封面URL"""
        if self.type == "MAJOR_TYPE_ARCHIVE" and self.archive:
            return self.archive.cover
        # 对于图文动态，取第一张图
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
        return self.module_author.name

    @property
    def author_face(self) -> str:
        return self.module_author.face

    @property
    def pub_ts(self) -> int:
        return self.module_author.pub_ts

    @property
    def major_info(self) -> dict[str, Any] | None:
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
        return self.modules.author_name

    @property
    def avatar(self) -> str:
        return self.modules.author_face

    @property
    def timestamp(self) -> int:
        return self.modules.pub_ts

    @property
    def desc_text(self) -> str | None:
        return self.modules.desc_text

    @property
    def _major(self) -> DynamicMajor | None:
        """内部辅助方法：安全转换 major 数据"""
        major_info = self.modules.major_info
        if not major_info:
            return None
        try:
            return convert(major_info, DynamicMajor)
        except Exception:
            # 如果转换失败（例如遇到了未定义的字段类型），静默失败以保证其他字段可用
            return None

    @property
    def title(self) -> str | None:
        if major := self._major:
            return major.title
        return None

    @property
    def text(self) -> str | None:
        if major := self._major:
            if t := major.text:
                return t
        # 如果 major 中没有文本，则返回 desc_text（普通动态正文）
        return self.desc_text

    @property
    def image_urls(self) -> list[str]:
        if major := self._major:
            return major.image_urls
        return []

    @property
    def cover_url(self) -> str | None:
        if major := self._major:
            return major.cover_url
        return None


class DynamicData(Struct):
    """动态项目"""

    item: DynamicInfo
    orig: DynamicInfo | None = None  # 转发的原动态
