from typing import Any
from dataclasses import replace
from typing_extensions import override

from nonebot import logger, require

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import template_to_pic

from . import resources
from .base import ParseResult, ImageRenderer, pconfig
from ..helper import UniHelper, UniMessage
from ..browser_retry import with_browser_retry

# Chromium 全页截图对超长页面有渲染上限：实测 CSS ~33000px 后内容开始丢失（画面留白）。
# 单页保守取 16000 CSS px，对应字符数阈值（见 _CHUNK_MAX_CHARS）。
_SAFE_PAGE_CSS_HEIGHT = 16000

# 每页文字字符数上限：按 _SAFE_PAGE_CSS_HEIGHT 反推。
# card 模板正文 28px 字号、1.8 行高、760px 行宽 → 每行 ~27 字、每行高 ~50px，
# 16000px / 50 ≈ 320 行 × 27 字 ≈ 8600 字符，保守取 6000。
_CHUNK_MAX_CHARS = 6000


class HtmlRenderer(ImageRenderer):
    """HTML 渲染器（Playwright 截图）。

    超长图文（如 B站 opus 长文，数百文字段落）渲染成单张巨图时，Chromium
    全页截图会在 ~33000 CSS px 后丢失内容（画面留白）。本渲染器对超长 graphics
    分页：按字符数拆成多块，每块单独渲染一张完整图，再各自切片合并转发。
    """

    @override
    async def render_image(self, result: ParseResult) -> bytes:
        await result.ensure_downloads_complete()

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

        短内容走基类（单图 + 切片 + render_contents）；graphics 文字总字符数超
        _CHUNK_MAX_CHARS 时分块，每块构造临时 ParseResult（保留头部上下文）单独渲染，
        各自切片后合并转发。
        """
        if not self._needs_paging(result):
            async for msg in super().render_messages(result):
                yield msg
            return

        chunks = self._chunk_graphics(result)
        logger.info(f"HtmlRenderer 分页: graphics 拆成 {len(chunks)} 页 (每页 ≤{_CHUNK_MAX_CHARS} 字符)")

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
        """graphics 文字总字符数超阈值才分页（图片型 graphics 不算）。"""
        total_chars = sum(len(g) for g in result.graphics if isinstance(g, str))
        return total_chars > _CHUNK_MAX_CHARS

    @staticmethod
    def _chunk_graphics(result: ParseResult) -> list[list[Any]]:
        """按字符数阈值把 graphics 拆成多块，尽量在段落边界切分。

        图片型 graphics（ImageContent）跟随相邻文字块，不单独计数。
        """
        chunks: list[list[Any]] = []
        current: list[Any] = []
        current_chars = 0
        for item in result.graphics:
            item_len = len(item) if isinstance(item, str) else 0
            # 当前块非空且加入后超阈值 → 收尾开新块
            if current and current_chars + item_len > _CHUNK_MAX_CHARS:
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(item)
            current_chars += item_len
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
