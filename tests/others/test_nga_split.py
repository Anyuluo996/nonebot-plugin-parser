"""验证 NGA 长图切片逻辑（纯 Pillow，但需 lazy import 触发插件初始化）"""

import io

import pytest
from PIL import Image

MAX_IMAGE_HEIGHT = 4000


def _make_long_png(width: int, height: int) -> bytes:
    """造一张带横线标记的长图 PNG bytes"""
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    for y in range(0, height, 1000):
        for x in range(width):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_split_long_image():
    """超高长图应被正确切片"""
    from nonebot_plugin_parser.renders.nga import Renderer

    raw = _make_long_png(720, 9500)
    slices = await Renderer._split_long_image(raw)
    assert len(slices) == 3, f"9500/{MAX_IMAGE_HEIGHT} 应切 3 片, 实际 {len(slices)}"
    total_h = 0
    for i, s in enumerate(slices):
        im = Image.open(io.BytesIO(s))
        w, h = im.size
        assert w == 720
        assert h <= MAX_IMAGE_HEIGHT, f"片{i} 超高: {h}"
        total_h += h
    assert total_h == 9500, f"拼接高度 {total_h} != 9500"


@pytest.mark.asyncio
async def test_no_split_when_short():
    """不超高的图原样返回单元素"""
    from nonebot_plugin_parser.renders.nga import Renderer

    raw = _make_long_png(720, 2000)
    slices = await Renderer._split_long_image(raw)
    assert len(slices) == 1
    assert slices[0] == raw


@pytest.mark.asyncio
async def test_split_boundary_exact():
    """高度正好等于阈值: 不切片"""
    from nonebot_plugin_parser.renders.nga import Renderer

    raw = _make_long_png(720, MAX_IMAGE_HEIGHT)
    slices = await Renderer._split_long_image(raw)
    assert len(slices) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
