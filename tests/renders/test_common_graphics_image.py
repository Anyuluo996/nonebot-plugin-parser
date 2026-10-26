"""验证 CommonRenderer 图文内嵌图的高度封顶与画布兜底。

回归：`_render_img_in_graphics` 只按内容宽度缩放，竖长图（长截图/长海报）可高达
数千 px，而 `_estimate_height` 只按 MAX_COVER_HEIGHT=800 预留 —— 画布不够时
PIL paste 越界部分被静默裁剪（图片下半截凭空消失）。修法：等比限高 + 逐项扩画布。

注意：nonebot_plugin_parser.* 的 import 放在测试函数内部，确保 conftest 的
init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import pytest
from PIL import Image as PILImage
from PIL import ImageDraw


def _result(graphics):
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="bilibili", display_name="哔哩哔哩"),
        author=Author(name="test"),
        title="t",
        url="https://example.com",
        graphics=graphics,
    )


def _img(path, alt=None):
    from nonebot_plugin_parser.parsers import ImageContent

    return ImageContent(path_task=path, alt=alt)


def _context(renderer, result, height=200):
    from nonebot_plugin_parser.renders.common import RenderContext

    image = PILImage.new("RGB", (renderer.DEFAULT_CARD_WIDTH, height), (255, 255, 255))
    return RenderContext(
        result=result,
        card_width=renderer.DEFAULT_CARD_WIDTH,
        content_width=renderer.DEFAULT_CARD_WIDTH - 2 * renderer.PADDING,
        image=image,
        draw=ImageDraw.Draw(image),
        y_pos=renderer.PADDING,
    )


@pytest.mark.asyncio
async def test_graphics_image_height_capped(tmp_path):
    """竖长图按 MAX_COVER_HEIGHT 等比封顶并居中，不再越出画布被裁剪。"""
    from nonebot_plugin_parser.renders.common import CommonRenderer

    renderer = CommonRenderer()
    renderer.load_resources()

    tall = tmp_path / "tall.png"
    PILImage.new("RGB", (600, 4000), (10, 20, 30)).save(tall)  # 宽 600 < 内容宽 750
    content = _img(tall)
    ctx = _context(renderer, _result([content]))

    await renderer._render_img_in_graphics(ctx, content)

    pasted_h = ctx.y_pos - renderer.PADDING - renderer.SECTION_SPACING
    assert pasted_h == renderer.MAX_COVER_HEIGHT, f"竖长图应限高到 {renderer.MAX_COVER_HEIGHT}px，实际 {pasted_h}"
    # 等比缩放: 600x4000 → 120x800
    assert ctx.image.height >= renderer.PADDING + pasted_h + renderer.SECTION_SPACING, "画布应已扩到容得下"
    # 居中: 内容区中心像素是图片色
    center_x = renderer.DEFAULT_CARD_WIDTH // 2
    bottom_y = renderer.PADDING + pasted_h - 1
    assert ctx.image.getpixel((center_x, bottom_y)) == (10, 20, 30), "图片底部应真实画出（未被裁剪、未偏移）"


@pytest.mark.asyncio
async def test_graphics_items_expand_canvas(tmp_path):
    """多个高图逐项兜底扩画布：每张都完整画出，后一张不被裁掉。"""
    from nonebot_plugin_parser.renders.common import CommonRenderer

    renderer = CommonRenderer()
    renderer.load_resources()

    tall = tmp_path / "tall.png"
    PILImage.new("RGB", (750, 4000), (200, 5, 5)).save(tall)
    contents = [_img(tall), _img(tall)]
    ctx = _context(renderer, _result(contents), height=200)

    for content in contents:
        # 与 _render_cover_or_images 的逐项兜底一致
        renderer._ensure_height_enough(ctx, renderer._graphics_item_height(ctx, content))
        await renderer._render_img_in_graphics(ctx, content)

    expected_bottom = renderer.PADDING + 2 * (renderer.MAX_COVER_HEIGHT + renderer.SECTION_SPACING)
    assert ctx.y_pos >= expected_bottom, f"两项图应推进到 {expected_bottom}px 之外，实际 {ctx.y_pos}"
    assert ctx.image.height >= ctx.y_pos, "画布高度应覆盖全部内容"
    # 第二张图最后一行应是图片色（没有被画布边界裁掉）
    last_row = ctx.y_pos - renderer.SECTION_SPACING - 1
    assert ctx.image.getpixel((renderer.DEFAULT_CARD_WIDTH // 2, last_row)) == (200, 5, 5)


@pytest.mark.asyncio
async def test_graphics_item_height_estimates(tmp_path):
    """逐项高度估算: 长文本按行数, 图片按限高。"""
    from nonebot_plugin_parser.renders.common import CommonRenderer

    renderer = CommonRenderer()
    renderer.load_resources()
    ctx = _context(renderer, _result([]))

    text = "\n".join(["正文"] * 10)
    text_px = renderer._graphics_item_height(ctx, text)
    assert text_px >= 10 * renderer.fontset.text.line_height, f"10 行长文本估算 {text_px}px 过低"

    img = _img(tmp_path / "x.png")
    assert renderer._graphics_item_height(ctx, img) == renderer.MAX_COVER_HEIGHT + renderer.SECTION_SPACING


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
