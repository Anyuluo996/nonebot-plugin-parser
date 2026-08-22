from typing import Any
from dataclasses import replace
from collections.abc import Sequence
from typing_extensions import override

from nonebot import logger, require

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import template_to_pic

from . import resources
from .base import ParseResult, ImageRenderer, pconfig
from ..helper import UniHelper, UniMessage
from ..parsers import ImageContent
from ..browser_retry import with_browser_retry

# htmlrender 0.7 起 template_to_pic 默认 device_scale_factor=2, 其共享浏览器实例
# 的截图光栅面在 ~16384 物理px 后不再绘制内容(实测 dpr=2 时 1600x26462 的图
# 只画到 y≈16381, 以下为纯背景; dpr=1 同内容画满)。安全页高按物理上限换算:
# 16384 / 2 = 8192 CSS px, 再留余量取 7000。
_SAFE_PAGE_CSS_HEIGHT = 7000

# 页面高度估算参数(与 card.html.jinja 紧凑版标定):
# 正文 17px 字号、1.6 行高 ≈ 27px/行, 卡片内容宽 ~735px → ~43 全角字符/行;
# 图文内嵌图模板限高 800px + 容器/内边距/alt ≈ 850px; 头部/标题/内边距固定开销 ~300px。
_CHARS_PER_LINE = 43
_LINE_HEIGHT_PX = 27
_GRAPHICS_IMG_EST_PX = 850
_PAGE_OVERHEAD_PX = 300


class HtmlRenderer(ImageRenderer):
    """HTML 渲染器（Playwright 截图）。

    超长图文（如 B站 opus 长文，数百文字段落 + 多图）渲染成单张巨图时，Chromium
    截图会超出光栅面上限（dpr=2 时 ~8192 CSS px）后不再绘制内容（画面留白但布局
    高度仍在，表现为"内容截断 + 空白尾图"）。本渲染器对超长 graphics 分页：按
    估算页高（文字行数 + 图片限高）拆成多块，每块单独渲染一张完整图，再各自切片
    合并转发。
    """

    @override
    async def render_image(self, result: ParseResult) -> bytes:
        # 仅等图片资源(封面/头像/内嵌图), 不等视频 — 视频下载可能很慢(mcdn 等 P2P 节点),
        # 卡片渲染不应被它阻塞。视频由 render_contents 独立发送(支持超时先发封面)。
        await result.ensure_downloads_complete(img_only=True)

        logo = resources.RESOURCES_DIR / f"{result.platform.name}.png"
        logo = logo.as_uri() if logo.exists() else None
        font = pconfig.custom_font or resources.DEFAULT_FONT_PATH
        font = font.as_uri() if font.exists() else None
        play_button = resources.DEFAULT_VIDEO_BUTTON_PATH.as_uri()

        # 包裹 with_browser_retry：浏览器子进程崩溃导致 transport 关闭时，
        # 强制重启全局浏览器实例并重试一次，避免单次解析失败。
        return await with_browser_retry(
            lambda: template_to_pic(
                template_path=str(self.templates_dir),
                template_name="card.html.jinja",
                templates={
                    "result": result,
                    "logo": logo,
                    "font": font,
                    "play_button": play_button,
                },
                pages={"viewport": {"width": 800, "height": 100}},
            )
        )

    @override
    async def render_messages(self, result: ParseResult):
        """超长 graphics 分页渲染，避免 Chromium 截图丢失内容。

        短内容走基类（单图 + 切片 + render_contents）；估算页高（文字 + 图片）
        超 _SAFE_PAGE_CSS_HEIGHT 时分块，每块构造临时 ParseResult（保留头部上下文）
        单独渲染，各自切片后合并转发。
        """
        if not self._needs_paging(result):
            async for msg in super().render_messages(result):
                yield msg
            return

        chunks = self._chunk_graphics(result)
        logger.info(f"HtmlRenderer 分页: graphics 拆成 {len(chunks)} 页 (每页 ≤{_SAFE_PAGE_CSS_HEIGHT}px 估算高度)")

        all_slices: list[bytes] = []
        for idx, chunk in enumerate(chunks):
            chunk_result = self._make_chunk_result(result, chunk, idx, len(chunks))
            raw = await self.render_image(chunk_result)
            slices = await self._split_long_image(raw)
            all_slices.extend(slices)

        url_text = ""
        if self.append_url:
            urls = (result.display_url, result.repost_display_url)
            url_text = "\n".join(url for url in urls if url)

        nodes: list[Any] = [UniHelper.img_seg(s) for s in all_slices]
        if url_text:
            nodes.append(url_text)
        yield UniMessage(UniHelper.construct_forward_message(nodes))

    def _needs_paging(self, result: ParseResult) -> bool:
        """graphics 估算页高（文字 + 图片）超安全高度才分页。"""
        return self._estimate_page_height(result.graphics) > _SAFE_PAGE_CSS_HEIGHT

    @staticmethod
    def _estimate_item_height(item: str | ImageContent) -> int:
        """估算单个 graphics 项的渲染高度（CSS px）。图片下载前拿不到真实尺寸,
        按模板限高统一估算(偏保守, 宁可多分一页也不超光栅上限)。"""
        import math

        if isinstance(item, str):
            lines = max(1, math.ceil(len(item) / _CHARS_PER_LINE))
            return lines * _LINE_HEIGHT_PX + 6  # 6px 段间距
        return _GRAPHICS_IMG_EST_PX

    @classmethod
    def _estimate_page_height(cls, items: Sequence[str | ImageContent]) -> int:
        return _PAGE_OVERHEAD_PX + sum(cls._estimate_item_height(i) for i in items)

    @classmethod
    def _chunk_graphics(cls, result: ParseResult) -> list[list[Any]]:
        """按估算页高把 graphics 拆成多块，尽量在段落边界切分。

        图片型 graphics（ImageContent）按限高估算计入, 不再只按字数——
        图片多的长文曾因此漏分页, 单页超高被光栅上限截断。"""
        chunks: list[list[Any]] = []
        current: list[Any] = []
        current_px = 0
        for item in result.graphics:
            item_px = cls._estimate_item_height(item)
            # 当前块非空且加入后超安全高度 → 收尾开新块
            if current and current_px + item_px > _SAFE_PAGE_CSS_HEIGHT - _PAGE_OVERHEAD_PX:
                chunks.append(current)
                current = []
                current_px = 0
            current.append(item)
            current_px += item_px
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _make_chunk_result(result: ParseResult, chunk: list[Any], idx: int, total: int) -> ParseResult:
        """构造单页 ParseResult：保留 platform/author/title 上下文，graphics=chunk。

        清空 contents/repost 避免重复渲染；分页标题标注页码。
        """
        page_title = result.title
        if total > 1:
            suffix = f"（{idx + 1}/{total}）"
            page_title = f"{result.title}{suffix}" if result.title else suffix
        return replace(
            result,
            title=page_title,
            graphics=chunk,
            contents=[],
            repost=None,
            render_image=None,
        )
