import io
import uuid
from abc import ABC, abstractmethod
from typing import Any, ClassVar
from pathlib import Path
from itertools import chain
from collections.abc import AsyncGenerator
from typing_extensions import override

from nonebot import logger

from ..config import pconfig
from ..helper import UniHelper, UniMessage, ForwardNodeInner
from ..parsers import ParseResult, AudioContent, ImageContent, VideoContent, DynamicContent
from ..exception import IgnoreException, DownloadException

# QQ NT 内核对单张图片高度有上限（约 4000-5000px），超长会 rich media transfer failed。
# 渲染后按此阈值垂直切片，单图直发、多图合并转发。
MAX_LONG_IMAGE_HEIGHT = 4000


class BaseRenderer(ABC):
    """统一的渲染器，将解析结果转换为消息"""

    templates_dir: ClassVar[Path] = Path(__file__).parent / "templates"
    """模板目录"""

    @abstractmethod
    async def render_messages(self, result: ParseResult) -> AsyncGenerator[UniMessage[Any], None]:
        """渲染解析结果"""
        if False:
            yield
        raise NotImplementedError

    async def render_contents(self, result: ParseResult) -> AsyncGenerator[UniMessage[Any], None]:
        """渲染媒体内容"""
        failed_count = 0
        forwardable_segs: list[ForwardNodeInner] = []
        dynamic_segs: list[ForwardNodeInner] = []

        for cont in chain(result.contents, result.repost.contents if result.repost else ()):
            try:
                match cont:
                    case VideoContent():
                        path = await cont.get_path()
                        yield UniMessage(UniHelper.video_seg(path))
                    case AudioContent():
                        path = await cont.get_path()
                        yield UniMessage(UniHelper.record_seg(path))
                    case ImageContent():
                        path = await cont.get_path()
                        forwardable_segs.append(UniHelper.img_seg(path))
                    case DynamicContent():
                        dynamic_path = await cont.get_gif_path() or await cont.get_path()
                        if dynamic_path.suffix.lower() == ".gif":
                            dynamic_segs.append(UniHelper.img_seg(dynamic_path))
                        else:
                            dynamic_segs.append(UniHelper.video_seg(dynamic_path))
            except IgnoreException:
                continue
            except DownloadException:
                failed_count += 1
                continue

        for cont in chain(result.graphics, result.repost.graphics if result.repost else ()):
            if isinstance(cont, str):
                forwardable_segs.append(cont)
                continue

            try:
                path = await cont.get_path()
            except IgnoreException:
                continue
            except DownloadException:
                failed_count += 1
                continue

            img_seg = UniHelper.img_seg(path)
            if cont.alt:
                img_seg += cont.alt
            forwardable_segs.append(img_seg)

        # 合并发送策略: 单个内容(图片或视频)一律直接发送, 不合并转发,
        # 以最大化协议端兼容性; 多个内容才合并转发 (need_forward_contents=False 时
        # 图片少于等于 4 张仍直接发送, 超过则强制合并转发避免刷屏)。
        all_segs = forwardable_segs + dynamic_segs
        if len(all_segs) == 1:
            # 单个内容: 直接发送, 不走合并转发
            yield UniMessage(all_segs)
        elif forwardable_segs and dynamic_segs:
            # 图片 + 视频混合: 合并转发
            yield UniMessage(UniHelper.construct_forward_message(all_segs))
        elif forwardable_segs:
            # 仅图片
            if pconfig.need_forward_contents or len(forwardable_segs) > 4:
                yield UniMessage(UniHelper.construct_forward_message(forwardable_segs))
            else:
                yield UniMessage(forwardable_segs)
        elif dynamic_segs:
            # 仅视频/动图
            if pconfig.need_forward_contents or len(dynamic_segs) > 1:
                yield UniMessage(UniHelper.construct_forward_message(dynamic_segs))
            else:
                yield UniMessage(dynamic_segs)

        if failed_count > 0:
            message = f"{failed_count} 项媒体下载失败"
            yield UniMessage(message)
            raise DownloadException(message)

    @property
    def append_url(self) -> bool:
        return pconfig.append_url


class ImageRenderer(BaseRenderer):
    """图片渲染器"""

    @abstractmethod
    async def render_image(self, result: ParseResult) -> bytes:
        """渲染图片"""
        raise NotImplementedError

    @override
    async def render_messages(self, result: ParseResult):
        image_seg, image_raw = await self.cache_or_render_image(result)

        # 切片长图，避免单图超高触发 NapCat rich media transfer failed
        slices = await self._split_long_image(image_raw)
        if len(slices) > 1:
            logger.debug(f"长图切片: {len(slices)} 张 (每张 ≤{MAX_LONG_IMAGE_HEIGHT}px)")

        url_text = ""
        if self.append_url:
            urls = (result.display_url, result.repost_display_url)
            url_text = "\n".join(url for url in urls if url)

        if len(slices) == 1:
            # 单图：直接发（保持原行为，URL 追加为文本）
            msg: UniMessage[Any] = UniMessage(image_seg)
            if url_text:
                msg += url_text
            yield msg
        else:
            # 多图：合并转发，URL 作为末尾文本节点
            segs = [UniHelper.img_seg(piece) for piece in slices]
            nodes: list[Any] = list(segs)
            if url_text:
                nodes.append(url_text)
            yield UniMessage(UniHelper.construct_forward_message(nodes))

        # 媒体内容
        async for message in self.render_contents(result):
            yield message

    @staticmethod
    async def _split_long_image(raw: bytes) -> list[bytes]:
        """长图按 MAX_LONG_IMAGE_HEIGHT 垂直切片，返回各片 bytes；不超高时原样返回单元素列表。

        Pillow 的 open/crop/save 是同步 CPU 操作，用 to_thread 避免阻塞事件循环。
        """
        import asyncio

        from PIL import Image

        def _do_split() -> list[bytes]:
            img = Image.open(io.BytesIO(raw))
            width, height = img.size
            if height <= MAX_LONG_IMAGE_HEIGHT:
                return [raw]
            pieces: list[bytes] = []
            for top in range(0, height, MAX_LONG_IMAGE_HEIGHT):
                bottom = min(top + MAX_LONG_IMAGE_HEIGHT, height)
                piece = img.crop((0, top, width, bottom))
                buf = io.BytesIO()
                piece.save(buf, format="PNG")
                pieces.append(buf.getvalue())
            return pieces

        return await asyncio.to_thread(_do_split)

    async def render_contents(self, result: ParseResult) -> AsyncGenerator[UniMessage[Any], None]:
        """ImageRenderer 系：graphics（文字段落 + 内嵌图）已渲染进卡片图，不再二次发出。

        只处理独立的 ``result.contents``（视频/音频/图片，这些不渲染进卡片图）。
        避免文字段落被当作独立消息重复发出（opus 长图文刷屏根因：397 个 str 段落
        构建合并转发 → NapCat 拒绝 → 降级逐条直发刷屏）。

        用临时清空 graphics 复用基类 render_contents，避免复制 contents 的分发逻辑。
        DefaultRenderer（纯文本模式）不继承 ImageRenderer，仍需 render_contents 发 graphics，
        不受此覆盖影响。
        """
        saved_graphics = result.graphics
        repost = result.repost
        saved_repost_graphics = repost.graphics if repost else None
        result.graphics = []
        if repost:
            repost.graphics = []
        try:
            async for message in super().render_contents(result):
                yield message
        finally:
            result.graphics = saved_graphics
            if repost:
                repost.graphics = saved_repost_graphics  # type: ignore[assignment]

    async def cache_or_render_image(self, result: ParseResult) -> tuple[Any, bytes]:
        """获取缓存图片，返回 (图片段, 原始 bytes)。

        原始 bytes 供长图切片复用，避免从 Image 段反解（其 raw/path 类型含 str|BytesIO，
        反解既不安全也丢类型）。图片段的构建逻辑与重构前保持一致：
        use_base64=True 时走 raw bytes，否则走文件路径。
        """
        if result.render_image is None:
            image_raw = await self.render_image(result)
            image_path = await self.save_img(image_raw)
            result.render_image = image_path
            if pconfig.use_base64:
                return UniHelper.img_seg(image_raw), image_raw
            return UniHelper.img_seg(image_path), image_raw

        # 走缓存路径：从已落盘文件读回 bytes 供切片，图片段仍按 use_base64 决定
        image_raw = result.render_image.read_bytes()
        if pconfig.use_base64:
            return UniHelper.img_seg(image_raw), image_raw
        return UniHelper.img_seg(result.render_image), image_raw

    @classmethod
    async def save_img(cls, raw: bytes) -> Path:
        """保存图片"""
        import aiofiles

        file_name = f"{uuid.uuid4().hex}.png"
        image_path = pconfig.cache_dir / file_name
        async with aiofiles.open(image_path, "wb+") as f:
            await f.write(raw)
        return image_path
