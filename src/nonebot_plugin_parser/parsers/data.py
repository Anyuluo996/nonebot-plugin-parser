from __future__ import annotations

import asyncio
from typing import Any, TypedDict
from asyncio import Task
from pathlib import Path
from datetime import datetime
from dataclasses import field, dataclass
from collections.abc import Callable, Iterator, Sequence, Awaitable

from nonebot import logger

from ..utils import fmt_duration
from ..config import pconfig
from ..exception import DownloadException


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
        # 下载段不设渲染层超时: 下载层自带重试预算(重试耗尽才抛 DownloadException),
        # 是下载耗时的唯一预算方。渲染层 wait_for 会 cancel 底层下载 task, 把仍在
        # 重试、马上就会成功的下载拦腰截断(2026-09-25 推特: twimg 视频重试 35s 后
        # 成功, 却先被 30s 超时判死, 整条解析报"媒体下载失败")。
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
    fallback: Callable[[], Sequence[ImageContent | DynamicContent]] | None = None
    """视频产出失败（下载/合成超时）时的降级出口。

    惰性工厂：仅在失败时调用，产物由渲染层按普通图/实况逐项直发（如幻灯片
    合成失败 → 原图 + 实况照片）。工厂内才创建下载任务，合成成功则零开销。
    None 表示无降级，按普通失败计数。
    """

    async def get_cover_path(self) -> Path | None:
        if self.cover is None:
            return None
        if isinstance(self.cover, Path):
            return self.cover
        timeout = pconfig.video_send_timeout
        try:
            self.cover = await self.cover if timeout <= 0 else await asyncio.wait_for(self.cover, timeout=timeout)
        except asyncio.TimeoutError as e:
            # wait_for 超时会 cancel 底层 Task, 但赋值语句不会执行 → 字段仍留着已取消的
            # Task。后续任何一次再取(如卡片先取封面再画网格)都会 await 已取消 Task 抛
            # CancelledError(BaseException), 穿透渲染层与 matcher 的 except Exception,
            # 让整条消息静默丢失。超时即置 None: 封面属装饰, 丢弃比二次崩溃好。
            self.cover = None
            raise DownloadException("封面下载超时") from e
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
    _gif_failed: bool = field(default=False, repr=False, compare=False)
    """GIF 转换是否已失败（超时/异常）。

    失败后把 ``gif_path`` 置 None 并置位本标记, 保证重复取值行为稳定:
    仅置 None 会让第二次取值退回 ``get_path()``(ugoira 的 zip), 被当视频发出。
    """

    async def get_gif_path(self) -> Path | None:
        if self._gif_failed:
            raise DownloadException("动图转换失败")
        if self.gif_path is None:
            return None
        if isinstance(self.gif_path, Path):
            return self.gif_path
        # 下载段不限时(与 get_path 同理, 下载层自带重试预算);
        # video_send_timeout 只约束转换段(palettegen+paletteuse 两遍 ffmpeg, 秒级)。
        await self.get_path()
        timeout = pconfig.video_send_timeout
        try:
            self.gif_path = (
                await self.gif_path if timeout <= 0 else await asyncio.wait_for(self.gif_path, timeout=timeout)
            )
        except asyncio.TimeoutError as e:
            # 同 get_cover_path: wait_for 已取消底层 Task, 不清理会二次抛 CancelledError
            self.gif_path = None
            self._gif_failed = True
            raise DownloadException("动图转换超时") from e
        except DownloadException:
            self.gif_path = None
            self._gif_failed = True
            raise
        except Exception as e:
            self.gif_path = None
            self._gif_failed = True
            raise DownloadException(f"GIF 转换失败: {e!r}") from e
        return self.gif_path

    async def get_cover_path(self) -> Path | None:
        if self.cover is None:
            return None
        if isinstance(self.cover, Path):
            return self.cover
        timeout = pconfig.video_send_timeout
        try:
            self.cover = await self.cover if timeout <= 0 else await asyncio.wait_for(self.cover, timeout=timeout)
        except asyncio.TimeoutError as e:
            # 理由同 VideoContent.get_cover_path: 避免重复 await 已取消的 Task
            self.cover = None
            raise DownloadException("封面下载超时") from e
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
                logger.debug(f"ugoira 缩略图提取失败(GIF 帧路径): {gif_path.name}", exc_info=True)

        # 3. 最后从 ZIP 提取第一帧
        zip_path = await self.get_path()
        if zip_path.exists() and self.frames:
            try:
                from ..utils import extract_ugoira_thumbnail

                return extract_ugoira_thumbnail(zip_path, self.frames)
            except Exception:
                logger.debug(f"ugoira 缩略图提取失败(ZIP 路径): {zip_path.name}", exc_info=True)

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
    contents: Sequence[MediaContent] = field(default_factory=list)
    """媒体内容"""
    graphics: Sequence[str | ImageContent | VideoContent] = field(default_factory=list)
    """图文内容 (htmlrender/common 渲染层支持内嵌 VideoContent 画封面, 本体走视频通道)"""
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

    def formatted_datetime(self, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
        """格式化时间戳。

        普通方法（非 property）—— 调用方需带括号 ``result.formatted_datetime()``。
        历史上误用 ``@property`` 但签名带 ``fmt`` 参数, 导致无法自定义格式且
        调用方只能拿到默认格式; 同时方法名拼写错误 ``formartted`` 已修正为
        ``formatted``。模板调用点已同步改为 ``result.formatted_datetime()``。
        """
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

    def _iter_extra_images(self) -> Iterator[ImageContent]:
        """extra 里内嵌的图片（楼层回复/回答正文 + 作者头像）。

        下载（``_iterate_download_coros``）与缓存有效性判定（``_has_pending_resources``）
        共用这一处：此前只下载不判 pending, 下载失败的内嵌图会以 Task 形态留在结果里,
        ``_cache_result`` 的 deepcopy 于是抛 TypeError, 让已成功发出的消息被记成
        「解析失败」并触发 L2 重试。
        """
        posts = self.extra.get("posts")
        if isinstance(posts, list):
            for post in posts:
                if not isinstance(post, dict):
                    continue
                for img in post.get("images") or ():
                    if isinstance(img, ImageContent):
                        yield img
                avatar = post.get("avatar")
                if isinstance(avatar, ImageContent):
                    yield avatar

        answers = self.extra.get("answers")
        if isinstance(answers, list):
            for ans in answers:
                if not isinstance(ans, dict):
                    continue
                for img in ans.get("content") or ():
                    if isinstance(img, ImageContent):
                        yield img
                avatar = ans.get("avatar")
                if isinstance(avatar, ImageContent):
                    yield avatar

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
            elif isinstance(gra, VideoContent) and gra.cover is not None:
                # 卡片要画视频封面(如小黑盒文章内嵌视频), 视频本体由 render_contents
                # 的视频通道带超时单独处理, 不在此等待
                yield gra.get_cover_path()

        # extra["posts"]/["answers"] 中的图片（NGA/贴吧回复楼层、知乎回答内嵌图 + 作者头像）
        for img in self._iter_extra_images():
            yield img.get_path()

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
            if isinstance(gra, VideoContent) and is_pending_path_task(gra.cover):
                return True
        # 检查 extra 内嵌图（楼层/回答）：不判这里会让失败的内嵌图留在结果里,
        # 缓存 deepcopy 时对残留 Task 抛 TypeError（见 _iter_extra_images 注释）
        for img in self._iter_extra_images():
            if is_pending_path_task(img.path_task):
                return True
        # GIF 转换已失败：结果本身不可复用（get_gif_path 恒抛），不写缓存
        for cont in self.contents:
            if isinstance(cont, DynamicContent) and cont._gif_failed:
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
    contents: Sequence[MediaContent]
    graphics: Sequence[str | ImageContent | VideoContent]
    timestamp: int | None
    url: str | None
    author: Author | None
    extra: dict[str, Any]
    repost: ParseResult | None
    cover_image: Path | Task[Path] | None
