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
from ..parsers import ImageContent, VideoContent
from ..browser_retry import with_browser_retry

# 根因实测(2026-08-22 对照实验): card 模板的 backdrop-filter 毛玻璃层在
# htmlrender 0.7 托管浏览器(dpr=2)下超过 ~16384 物理 px 纹理上限后不再绘制
# —— 同一张 11271 CSS 卡片带 backdrop-filter 只画到 73%(y=16381), 去掉
# backdrop-filter 后 100% 画满; 无 backdrop-filter 的 nga/tieba/zhihu 模板
# 实测 17357 CSS(34714 物理)仍完整绘制。即安全页高 = 16384/2 = 8192 CSS px,
# 再留余量取 7000。
_SAFE_PAGE_CSS_HEIGHT = 7000

# 页面高度估算参数(与 card.html.jinja 紧凑版标定):
# 正文 17px 字号、1.5 行高 ≈ 26px/行, 卡片内容宽 ~735px → ~43 全角字符/行;
# 图文内嵌图模板限高 800px + 圆角容器/alt ≈ 850px; 头部/标题/内边距固定开销 ~300px。
_CHARS_PER_LINE = 43
_LINE_HEIGHT_PX = 26
_GRAPHICS_IMG_EST_PX = 850
_PAGE_OVERHEAD_PX = 300


class HtmlRenderer(ImageRenderer):
    """HTML 渲染器（Playwright 截图）。

    超长图文（如 B站 opus 长文，数百文字段落 + 多图）渲染成单张巨图时，card
    模板的 backdrop-filter 大图层超过浏览器纹理上限（dpr=2 时 ~8192 CSS px）
    后不再绘制内容（画面留白但布局高度仍在，表现为"内容截断 + 空白尾图"）。
    本渲染器对超长 graphics 分页：按估算页高（文字行数 + 图片限高）拆成多块，
    每块单独渲染一张完整图，再各自切片合并转发。
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

        短内容走基类（单图 + 切片 + render_contents）；估算页高（文字 + 图片 +
        引用转发）超 _SAFE_PAGE_CSS_HEIGHT 时分块，每块构造临时 ParseResult（保留
        头部上下文）单独渲染，各自切片后合并转发。引用转发单独占末页，保证引用
        推文的作者/正文不丢。
        """
        if not self._needs_paging(result):
            async for msg in super().render_messages(result):
                yield msg
            return

        chunks = self._chunk_graphics(result)
        # 引用转发单独成一页: 塞进最后一页会和该页正文一起逼近光栅上限, 而每页
        # 清空 repost 又会让引用推文的作者/正文永久丢失(X 长 Article 事故)。
        if result.repost is not None:
            chunks.append([])
        logger.info(f"HtmlRenderer 分页: graphics 拆成 {len(chunks)} 页 (每页 ≤{_SAFE_PAGE_CSS_HEIGHT}px 估算高度)")

        all_slices: list[bytes] = []
        for idx, chunk in enumerate(chunks):
            is_repost_page = result.repost is not None and idx == len(chunks) - 1
            chunk_result = self._make_chunk_result(result, chunk, idx, len(chunks), keep_repost=is_repost_page)
            raw = await self.render_image(chunk_result)
            slices = await self._split_long_image(raw)
            all_slices.extend(slices)

        url_text = ""
        if self.append_url:
            urls = (result.display_url, result.repost_display_url)
            url_text = "\n".join(url for url in urls if url)

        nodes: list[Any] = await self._slice_segs(all_slices)
        if url_text:
            nodes.append(url_text)
        for ref in UniHelper.construct_forward_messages(nodes):
            yield UniMessage(ref)

        # 分页分支只渲染了卡片切片, contents(视频/音频等)仍需走视频/音频通道,
        # 否则长文 Article 内嵌视频会被静默丢弃 (ImageRenderer.render_contents
        # 的覆写会顺便把 graphics 里的视频剥进 contents)
        async for message in self.render_contents(result):
            yield message

    def _needs_paging(self, result: ParseResult) -> bool:
        """graphics（文字 + 图片）+ 引用转发的估算页高超安全高度才分页。"""
        return (
            self._estimate_page_height(result.graphics) + self._estimate_repost_height(result.repost)
            > _SAFE_PAGE_CSS_HEIGHT
        )

    @staticmethod
    def _estimate_item_height(item: str | ImageContent | VideoContent) -> int:
        """估算单个 graphics 项的渲染高度（CSS px）。图片下载前拿不到真实尺寸,
        按模板限高统一估算(偏保守, 宁可多分一页也不超光栅上限)。

        文字项必须逐行估算: 模板是 ``white-space: pre-wrap``, 换行直接占一整行,
        而旧实现只按 ``len(item)/_CHARS_PER_LINE`` 折行 —— Lofter/酷安/ILLU 把
        整篇正文塞进单个 graphics 字符串(几十上百个换行被算成一两行), 长文因此
        被严重低估 → 不分页 → 超过光栅上限后尾部静默空白。
        """
        import math

        if isinstance(item, str):
            # 空行本身占一行, 所以每行至少算 1
            lines = sum(max(1, math.ceil(len(line) / _CHARS_PER_LINE)) for line in item.splitlines())
            return max(lines, 1) * _LINE_HEIGHT_PX + 4  # 4px 段间距
        return _GRAPHICS_IMG_EST_PX

    @classmethod
    def _estimate_page_height(cls, items: Sequence[str | ImageContent | VideoContent]) -> int:
        return _PAGE_OVERHEAD_PX + sum(cls._estimate_item_height(i) for i in items)

    @classmethod
    def _estimate_repost_height(cls, repost: ParseResult | None) -> int:
        """估算引用转发卡片的渲染高度（CSS px, 保守粗算）。

        分页判断必须计入 repost: 正文 + 引用推文拼在同一页时同样会超光栅上限;
        而分页时若把 repost 一律清空, X 长 Article 引用的推文作者/正文会永久丢失。
        这里只按 作者行/标题/正文/内嵌图数量 粗算(引用卡片字号更小, 实际更低,
        宁可多分一页), 嵌套引用递归计入。
        """
        if repost is None:
            return 0
        height = cls._estimate_page_height(repost.graphics)  # 含引用卡片自身固定开销
        if repost.author:
            height += _LINE_HEIGHT_PX * 2  # 头像行 + 名称/时间
        if repost.title:
            height += cls._estimate_item_height(repost.title)
        if repost.text:
            height += cls._estimate_item_height(repost.text)
        if repost.contents:
            height += _GRAPHICS_IMG_EST_PX  # 卡片只画一张封面/图片网格, 按单项图估
        return height + cls._estimate_repost_height(repost.repost)

    @classmethod
    def _chunk_graphics(cls, result: ParseResult) -> list[list[Any]]:
        """按估算页高把 graphics 拆成多块，尽量在段落边界切分。

        图片型 graphics（ImageContent）按限高估算计入, 不再只按字数——
        图片多的长文曾因此漏分页, 单页超高被光栅上限截断。

        单个超长文字项先拆段再装箱: 只装箱不拆项时, 整篇正文塞在一个 graphics
        字符串里(Lofter/酷安/ILLU 的整篇正文)依然只有 1 块, 分页等于没做。"""
        max_page_px = _SAFE_PAGE_CSS_HEIGHT - _PAGE_OVERHEAD_PX
        items: list[Any] = []
        for item in result.graphics:
            if isinstance(item, str) and cls._estimate_item_height(item) > max_page_px:
                items.extend(cls._split_long_text(item))
            else:
                items.append(item)  # 图片/视频项不可拆(拆开就画不全了)

        chunks: list[list[Any]] = []
        current: list[Any] = []
        current_px = 0
        for item in items:
            item_px = cls._estimate_item_height(item)
            # 当前块非空且加入后超安全高度 → 收尾开新块
            if current and current_px + item_px > max_page_px:
                chunks.append(current)
                current = []
                current_px = 0
            current.append(item)
            current_px += item_px
        if current:
            chunks.append(current)
        return chunks

    @classmethod
    def _split_long_text(cls, text: str) -> list[str]:
        """把单个超长文字项按行切成多段（每段估算高度 ≤ 单页可用高度）。

        优先在 ``\\n`` 处切; 单行本身超宽(模板自动折行)时按 ``_CHARS_PER_LINE``
        硬切成多个"行", 否则根本切不动。每段仍是独立 graphics 项(模板各自成
        <p>), 段内换行原样保留。
        """
        import math

        max_px = _SAFE_PAGE_CSS_HEIGHT - _PAGE_OVERHEAD_PX - 4  # 扣掉段间距
        max_lines = max(1, max_px // _LINE_HEIGHT_PX)
        max_chars = max_lines * _CHARS_PER_LINE

        segments: list[str] = []
        buf: list[str] = []
        buf_lines = 0
        for line in text.splitlines():
            # 单行超宽(说明源文本没换行) → 按行宽硬切, 切点处补一个换行
            pieces = [line[i : i + max_chars] for i in range(0, len(line), max_chars)] or [""]
            for piece in pieces:
                piece_lines = max(1, math.ceil(len(piece) / _CHARS_PER_LINE))
                if buf and buf_lines + piece_lines > max_lines:
                    segments.append("\n".join(buf))
                    buf = []
                    buf_lines = 0
                buf.append(piece)
                buf_lines += piece_lines
        if buf:
            segments.append("\n".join(buf))
        return segments

    @staticmethod
    def _make_chunk_result(
        result: ParseResult, chunk: list[Any], idx: int, total: int, keep_repost: bool = False
    ) -> ParseResult:
        """构造单页 ParseResult：保留 platform/author/title 上下文，graphics=chunk。

        清空 contents 避免重复渲染；分页标题标注页码。repost 默认置 None 防止每页
        重复画引用卡片, 只有专门承载引用转发的末页(keep_repost=True)保留 —— 否则
        X 长 Article 引用的推文作者/正文在分页后永久丢失。
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
            repost=result.repost if keep_repost else None,
            render_image=None,
        )
