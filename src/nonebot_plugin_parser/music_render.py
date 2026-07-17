"""点歌搜索列表的 PIL 图片渲染。

独立于 ``renders/common.py`` 的 ``ParseResult`` 流水线 —— 点歌列表不是解析结果,
是一次性候选展示。复用 ``renders/common.py`` 的 ``FontSet`` 和默认字体以保证视觉
风格一致。

布局:
- 顶部标题: 「🔍 搜索: <keyword>」
- 编号列表（1-10 全局连续）: 「序号. 歌名 - 歌手」每行一首,右侧标注平台来源
- 不展示任何失败诊断（用户原则: 只暴露成功候选）
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import pconfig
from .music_search import SearchItem

# 布局常量
_CARD_WIDTH = 700
_PADDING = 25
_TITLE_FONT_SIZE = 30
_ITEM_FONT_SIZE = 24
_TAG_FONT_SIZE = 18
_LINE_HEIGHT = 38
_TITLE_LINE_HEIGHT = 50
_HEADER_HEIGHT = 30
_BOTTOM_PADDING = 25

# 颜色
_BG_COLOR = (255, 255, 255)
_TITLE_COLOR = (102, 51, 153)
_ITEM_COLOR = (51, 51, 51)
_INDEX_COLOR = (0, 122, 255)
_TAG_BG_COLORS: dict[str, tuple[int, int, int]] = {
    "netease": (238, 73, 73),  # 网易云红
    "qqmusic": (31, 162, 223),  # QQ 音乐蓝
    "kugou": (31, 178, 106),  # 酷狗绿
}
_TAG_TEXT_COLOR = (255, 255, 255)

# 平台短标签
_TAG_TEXT: dict[str, str] = {
    "netease": "网易云",
    "qqmusic": "QQ",
    "kugou": "酷狗",
}

# 全局字体路径缓存（避免每次渲染重新查找 TTF）
_FONT_PATH: Path | None = None


def _get_font_path() -> Path:
    """字体路径（自定义优先,否则用默认字体）。"""
    global _FONT_PATH
    if _FONT_PATH is None:
        if pconfig.custom_font:
            _FONT_PATH = pconfig.custom_font
        else:
            from .renders import resources

            _FONT_PATH = resources.DEFAULT_FONT_PATH
    return _FONT_PATH


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """按 size 取字体（用于列表项/标题）。"""
    return ImageFont.truetype(_get_font_path(), size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    """测量文本宽度（兼容 Pillow < 10 的 getsize 与 >= 10 的 getbbox）。"""
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """超宽文本截断加省略号（中英文混排按字符逐个截断）。"""
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    ellipsis_w = _text_width(draw, ellipsis, font)
    out = ""
    for ch in text:
        if _text_width(draw, out + ch, font) + ellipsis_w > max_width:
            return out + ellipsis
        out += ch
    return out


def _draw_tag(draw: ImageDraw.ImageDraw, x: int, y: int, platform: str) -> int:
    """绘制平台来源小标签,返回标签总宽度（含右边距）。"""
    text = _TAG_TEXT.get(platform, platform)
    font = _get_font(_TAG_FONT_SIZE)
    text_w = _text_width(draw, text, font)
    pad_x, pad_y = 6, 3
    tag_w = text_w + pad_x * 2
    tag_h = _TAG_FONT_SIZE + pad_y * 2
    bg = _TAG_BG_COLORS.get(platform, (150, 150, 150))
    # 圆角矩形
    draw.rounded_rectangle((x, y, x + tag_w, y + tag_h), radius=6, fill=bg)
    # 文本垂直居中
    text_y = y + pad_y - 1
    draw.text((x + pad_x, text_y), text, font=font, fill=_TAG_TEXT_COLOR)
    return tag_w + 8  # 含右侧间距


def render_search_list_image(keyword: str, items: list[SearchItem]) -> bytes:
    """渲染搜索候选列表为 PNG bytes。

    Args:
        keyword: 原始搜索词。
        items: 已合并去重的候选列表（按平台顺序）。

    Returns:
        PNG 图片 bytes。

    约束:
    - 不展示任何失败信息,只渲染成功的 items。
    - items 为空时上层应已拦截,这里不做保护（防御性画空图）。
    """
    title_font = _get_font(_TITLE_FONT_SIZE)
    index_font = _get_font(_ITEM_FONT_SIZE)
    item_font = _get_font(_ITEM_FONT_SIZE)

    content_width = _CARD_WIDTH - _PADDING * 2
    # 临时 draw 用于测量（最终绘制时重建）
    tmp_img = Image.new("RGB", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)

    title_text = f"🔍 搜索: {keyword}"
    # 估算高度: 标题 + 每项一行 + 上下 padding
    n_items = len(items)
    height = _PADDING + _TITLE_LINE_HEIGHT + _HEADER_HEIGHT + n_items * _LINE_HEIGHT + _BOTTOM_PADDING

    image = Image.new("RGB", (_CARD_WIDTH, height), _BG_COLOR)
    draw = ImageDraw.Draw(image)

    # 标题
    draw.text((_PADDING, _PADDING), title_text, font=title_font, fill=_TITLE_COLOR)
    y = _PADDING + _TITLE_LINE_HEIGHT + 10

    # 分隔线
    draw.line(
        (_PADDING, y, _CARD_WIDTH - _PADDING, y),
        fill=(230, 230, 230),
        width=1,
    )
    y += 15

    # 列表项
    for i, item in enumerate(items, start=1):
        index_str = f"{i}."
        # 序号
        draw.text((_PADDING, y), index_str, font=index_font, fill=_INDEX_COLOR)
        index_w = _text_width(draw, index_str + " ", index_font)

        # 右侧平台标签（先算宽度,以便左侧歌名截断）
        tag_total_w = _text_width(draw, _TAG_TEXT.get(item.platform, item.platform), _get_font(_TAG_FONT_SIZE)) + 20
        text_max_width = content_width - index_w - tag_total_w - 10

        song_text = _truncate(tmp_draw, item.display, item_font, text_max_width)
        draw.text((_PADDING + index_w, y), song_text, font=item_font, fill=_ITEM_COLOR)

        # 平台标签（右对齐）
        tag_x = _CARD_WIDTH - _PADDING - tag_total_w + 8
        _draw_tag(draw, tag_x, y + 3, item.platform)

        y += _LINE_HEIGHT

    tmp_img.close()

    # PNG 编码是同步 CPU 操作,丢进线程池避免阻塞事件循环
    output = BytesIO()
    image.save(output, format="PNG")
    png_bytes = output.getvalue()
    image.close()
    return png_bytes


async def render_search_list_image_async(keyword: str, items: list[SearchItem]) -> bytes:
    """异步包装: 在线程池中执行 PIL 渲染。"""
    return await asyncio.to_thread(render_search_list_image, keyword, items)
