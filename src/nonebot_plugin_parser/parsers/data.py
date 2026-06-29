from __future__ import annotations

import asyncio
from typing import Any, TypedDict
from asyncio import Task
from pathlib import Path
from datetime import datetime
from dataclasses import field, dataclass
from collections.abc import Iterator, Awaitable

from ..utils import fmt_duration


def repr_path_task(path_task: Path | Task[Path]) -> str:
    if isinstance(path_task, Path):
        return f"path={path_task.name}"
    else:
        return f"task={path_task.get_name()}, done={path_task.done()}"


def is_pending_path_task(path_task: Path | Task[Path] | None) -> bool:
    """检查是否是待处理的下载任务"""
    return isinstance(path_task, Task)


@dataclass(repr=False, slots=True)
class MediaContent:
    path_task: Path | Task[Path]

    async def get_path(self) -> Path:
        if isinstance(self.path_task, Path):
            return self.path_task
        self.path_task = await self.path_task
        return self.path_task

    @property
    def path_uri(self):
        if isinstance(self.path_task, Path):
            return self.path_task.as_uri()

    def __repr__(self) -> str:
        prefix = self.__class__.__name__
        return f"{prefix}({repr_path_task(self.path_task)})"


@dataclass(repr=False, slots=True)
class AudioContent(MediaContent):
    """音频内容"""

    duration: float = 0.0


@dataclass(repr=False, slots=True)
class VideoContent(MediaContent):
    """视频内容"""

    cover: Path | Task[Path] | None = None
    """视频封面"""
    duration: float = 0.0
    """时长 单位: 秒"""

    async def get_cover_path(self) -> Path | None:
        if self.cover is None:
            return None
        if isinstance(self.cover, Path):
            return self.cover
        self.cover = await self.cover
        return self.cover

    @property
    def cover_path_uri(self):
        if isinstance(self.cover, Path):
            return self.cover.as_uri()

    @property
    def display_duration(self) -> str:
        return f"时长: {fmt_duration(self.duration)}"

    def __repr__(self) -> str:
        repr = f"VideoContent({repr_path_task(self.path_task)}"
        if self.cover is not None:
            repr += f", cover={repr_path_task(self.cover)}"
        return repr + ")"


@dataclass(repr=False, slots=True)
class ImageContent(MediaContent):
    """图片内容"""

    alt: str | None = None
    """图片描述 用于图文"""


@dataclass(repr=False, slots=True)
class DynamicContent(MediaContent):
    """动态内容 视频格式 后续转 gif"""

    gif_path: Path | Task[Path] | None = None
    cover: Path | Task[Path] | None = None
    frames: list[dict[str, Any]] | None = None
    """Ugoira 帧信息列表 [{"file": "000000.jpg", "delay": 1000}]，用于提取缩略图"""

    async def get_gif_path(self) -> Path | None:
        if self.gif_path is None:
            return None
        if isinstance(self.gif_path, Path):
            return self.gif_path
        self.gif_path = await self.gif_path
        return self.gif_path

    async def get_cover_path(self) -> Path | None:
        if self.cover is None:
            return None
        if isinstance(self.cover, Path):
            return self.cover
        self.cover = await self.cover
        return self.cover

    @property
    def gif_path_uri(self):
        if isinstance(self.gif_path, Path):
            return self.gif_path.as_uri()

    async def get_thumbnail_path(self) -> Path | None:
        """获取缩略图路径：优先封面，其次 GIF 首帧，最后从 ZIP 提取首帧"""
        # 1. 优先使用封面
        cover = await self.get_cover_path()
        if cover and cover.exists():
            return cover

        # 2. 其次使用 GIF 缩略图（从 GIF 第一帧提取）
        gif_path = await self.get_gif_path()
        if gif_path and gif_path.exists() and self.frames:
            try:
                from ..utils import extract_ugoira_thumbnail

                return extract_ugoira_thumbnail(gif_path.with_suffix(".zip"), self.frames)
            except Exception:
                pass

        # 3. 最后从 ZIP 提取第一帧
        zip_path = await self.get_path()
        if zip_path.exists() and self.frames:
            try:
                from ..utils import extract_ugoira_thumbnail

                return extract_ugoira_thumbnail(zip_path, self.frames)
            except Exception:
                pass

        return None

    @property
    def cover_path_uri(self):
        if isinstance(self.cover, Path):
            return self.cover.as_uri()

    def __repr__(self) -> str:
        repr = f"DynamicContent({repr_path_task(self.path_task)}"
        if self.gif_path is not None:
            repr += f", gif={repr_path_task(self.gif_path)}"
        if self.cover is not None:
            repr += f", cover={repr_path_task(self.cover)}"
        return repr + ")"


@dataclass(slots=True)
class Platform:
    """平台信息"""

    name: str
    """ 平台名称 """
    display_name: str
    """ 平台显示名称 """


@dataclass(repr=False, slots=True)
class Author:
    """作者信息"""

    name: str
    """作者名称"""
    avatar: Path | Task[Path] | None = None
    """作者头像 URL 或本地路径"""
    description: str | None = None
    """作者个性签名等"""

    async def get_avatar_path(self) -> Path | None:
        if self.avatar is None:
            return None
        if isinstance(self.avatar, Path):
            return self.avatar
        self.avatar = await self.avatar
        return self.avatar

    @property
    def avatar_path_uri(self):
        if isinstance(self.avatar, Path):
            return self.avatar.as_uri()

    def __repr__(self) -> str:
        repr = f"Author(name={self.name}"
        if self.avatar:
            repr += f", avatar_{repr_path_task(self.avatar)}"
        if self.description:
            repr += f", description={self.description}"
        return repr + ")"


@dataclass(repr=False, slots=True)
class ParseResult:
    """完整的解析结果"""

    platform: Platform
    """平台信息"""
    author: Author | None = None
    """作者信息"""
    title: str | None = None
    """标题"""
    text: str | None = None
    """文本内容"""
    timestamp: int | None = None
    """发布时间戳, 秒"""
    url: str | None = None
    """来源链接"""
    contents: list[MediaContent] = field(default_factory=list)
    """媒体内容"""
    graphics: list[str | ImageContent] = field(default_factory=list)
    """图文内容"""
    extra: dict[str, Any] = field(default_factory=dict)
    """额外信息"""
    repost: ParseResult | None = None
    """转发的内容"""
    render_image: Path | None = None
    """渲染图片"""
    cover_image: Path | Task[Path] | None = None
    """渲染专用封面（如音乐歌曲封面）。

    仅供渲染器画卡片使用，发送流程（render_contents）不读取它，
    因此不会被当作独立图片消息发出。优先级高于 video/dynamic 的 cover。
    """

    async def get_cover_image_path(self) -> Path | None:
        """获取渲染专用封面路径"""
        if self.cover_image is None:
            return None
        if isinstance(self.cover_image, Path):
            return self.cover_image
        self.cover_image = await self.cover_image
        return self.cover_image

    @property
    def cover_image_uri(self):
        if isinstance(self.cover_image, Path):
            return self.cover_image.as_uri()

    @property
    def header(self) -> str | None:
        """头信息 仅用于 default render"""
        header = self.platform.display_name
        if self.author:
            header += f" @{self.author.name}"
        if self.title:
            header += f" | {self.title}"
        return header

    @property
    def display_url(self) -> str | None:
        return f"链接: {self.url}" if self.url else None

    @property
    def repost_display_url(self) -> str | None:
        return f"原帖: {self.repost.url}" if self.repost and self.repost.url else None

    @property
    def extra_info(self) -> str | None:
        return self.extra.get("info")

    @property
    def video_contents(self) -> list[VideoContent]:
        return [cont for cont in self.contents if isinstance(cont, VideoContent)]

    @property
    def img_contents(self) -> list[ImageContent]:
        return [cont for cont in self.contents if isinstance(cont, ImageContent)]

    @property
    def audio_contents(self) -> list[AudioContent]:
        return [cont for cont in self.contents if isinstance(cont, AudioContent)]

    @property
    def dynamic_contents(self) -> list[DynamicContent]:
        return [cont for cont in self.contents if isinstance(cont, DynamicContent)]

    @property
    def formartted_datetime(self, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
        """格式化时间戳"""
        return datetime.fromtimestamp(self.timestamp).strftime(fmt) if self.timestamp is not None else None

    async def cover_path(self) -> Path | None:
        """获取封面路径"""
        # 优先使用渲染专用封面（音乐歌曲封面等，不随消息发送）
        if self.cover_image is not None:
            if path := await self.get_cover_image_path():
                return path
        for cont in self.contents:
            if isinstance(cont, VideoContent):
                return await cont.get_cover_path()
            if isinstance(cont, DynamicContent):
                return await cont.get_thumbnail_path()
        return None

    def _iterate_download_coros(self, img_only: bool = False) -> Iterator[Awaitable[Path | None]]:
        if author := self.author:
            if author.avatar:
                yield author.get_avatar_path()

        # 渲染专用封面（音乐歌曲封面），无论 img_only 与否都要下载
        if self.cover_image is not None:
            yield self.get_cover_image_path()

        for cont in self.contents:
            if isinstance(cont, DynamicContent):
                if not img_only:
                    yield cont.get_path()
                    if cont.gif_path is not None:
                        yield cont.get_gif_path()
                    if cont.cover is not None:
                        yield cont.get_cover_path()
                elif cont.cover is not None:
                    yield cont.get_cover_path()
                continue

            if not img_only:
                yield cont.get_path()
                if isinstance(cont, VideoContent) and cont.cover is not None:
                    yield cont.get_cover_path()
            elif isinstance(cont, VideoContent):
                yield cont.get_cover_path()
            elif isinstance(cont, ImageContent):
                yield cont.get_path()

        for gra in self.graphics:
            if isinstance(gra, ImageContent):
                yield gra.get_path()

        # extra["posts"] 中的图片（如 NGA 回复楼层内嵌图）
        for post in self.extra.get("posts", []) if isinstance(self.extra.get("posts"), list) else ():
            for img in post.get("images", []) if isinstance(post, dict) else ():
                if isinstance(img, ImageContent):
                    yield img.get_path()

        # extra["answers"] 中的图片（如知乎问题页高赞回答内嵌图 + 作者头像）
        for ans in self.extra.get("answers", []) if isinstance(self.extra.get("answers"), list) else ():
            if isinstance(ans, dict):
                for img in ans.get("content", []) if isinstance(ans.get("content"), list) else ():
                    if isinstance(img, ImageContent):
                        yield img.get_path()
                if isinstance(ans.get("avatar"), ImageContent):
                    yield ans["avatar"].get_path()

        if self.repost is not None:
            yield from self.repost._iterate_download_coros(img_only)

    async def ensure_downloads_complete(
        self,
        *,
        img_only: bool = False,
        suppress_errors: bool = True,
    ) -> None:
        await asyncio.gather(
            *self._iterate_download_coros(img_only),
            return_exceptions=suppress_errors,
        )

    def _has_pending_resources(self) -> bool:
        """检查是否有待下载的资源"""
        # 检查作者头像
        if self.author and is_pending_path_task(self.author.avatar):
            return True
        # 检查渲染专用封面
        if is_pending_path_task(self.cover_image):
            return True
        # 检查内容
        for cont in self.contents:
            if isinstance(cont, MediaContent) and is_pending_path_task(cont.path_task):
                return True
            if isinstance(cont, VideoContent) and is_pending_path_task(cont.cover):
                return True
            if isinstance(cont, DynamicContent):
                if is_pending_path_task(cont.gif_path):
                    return True
                if is_pending_path_task(cont.cover):
                    return True
        # 检查图文
        for gra in self.graphics:
            if isinstance(gra, ImageContent) and is_pending_path_task(gra.path_task):
                return True
        # 检查转发
        if self.repost and self.repost._has_pending_resources():
            return True
        return False

    def is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        # 如果有待下载的资源，缓存无效
        if self._has_pending_resources():
            return False
        # 检查渲染图片是否存在
        if self.render_image is None or not self.render_image.exists():
            return False
        return True

    @property
    def content_type(self) -> str | None:
        """获取内容类型 (允许解析器通过 extra 显式指定)"""
        content_type = self.extra.get("content_type")

        if content_type is None:
            if self.video_contents:
                return "视频"
            elif self.dynamic_contents:
                return "动态"
            elif self.graphics:
                return "图文"
            elif self.img_contents:
                return "动态"
            elif self.repost:
                return "动态"

        return content_type

    def __repr__(self) -> str:
        return (
            f"platform: {self.platform.display_name}, "
            f"timestamp: {self.timestamp}, "
            f"title: {self.title}, "
            f"text: {self.text}, "
            f"url: {self.url}, "
            f"author: {self.author}, "
            f"contents: {self.contents}, "
            f"graphics: {self.graphics}, "
            f"extra: {self.extra}, "
            f"repost: <<<<<<<{self.repost}>>>>>>, "
            f"render_image: {self.render_image.name if self.render_image else 'None'}"
        )


class ParseResultKwargs(TypedDict, total=False):
    title: str | None
    text: str | None
    contents: list[MediaContent]
    graphics: list[str | ImageContent]
    timestamp: int | None
    url: str | None
    author: Author | None
    extra: dict[str, Any]
    repost: ParseResult | None
    cover_image: Path | Task[Path] | None
