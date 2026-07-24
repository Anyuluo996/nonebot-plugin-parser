"""验证长图切片逻辑（纯 Pillow，但需 lazy import 触发插件初始化）。

切片方法原为 NGA 私有实现，现已提升到基类 ImageRenderer._split_long_image，
供所有渲染器（NGA/贴吧/知乎/B站等）复用。这里直接测基类。
"""

import io

import pytest
from PIL import Image

MAX_LONG_IMAGE_HEIGHT = 4000


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
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_long_png(720, 9500)
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) == 3, f"9500/{MAX_LONG_IMAGE_HEIGHT} 应切 3 片, 实际 {len(slices)}"
    total_h = 0
    for i, s in enumerate(slices):
        im = Image.open(io.BytesIO(s))
        w, h = im.size
        assert w == 720
        assert h <= MAX_LONG_IMAGE_HEIGHT, f"片{i} 超高: {h}"
        total_h += h
    assert total_h == 9500, f"拼接高度 {total_h} != 9500"


@pytest.mark.asyncio
async def test_no_split_when_short():
    """不超高的图原样返回单元素"""
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_long_png(720, 2000)
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) == 1
    assert slices[0] == raw


@pytest.mark.asyncio
async def test_split_boundary_exact():
    """高度正好等于阈值: 不切片"""
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_long_png(720, MAX_LONG_IMAGE_HEIGHT)
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) == 1


@pytest.mark.asyncio
async def test_split_merges_short_tail():
    """最后一片过矮（残片）时并入倒数第二片，避免发出小空白图。

    如 8100px → [0-4000, 4000-8100]，而非 [0-4000, 4000-8000, 8000-8100(100px)]。
    """
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_long_png(720, 8100)  # 残片 100px < 800 阈值
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) == 2, f"矮残片应并入，期望 2 片，实际 {len(slices)}"
    total_h = sum(Image.open(io.BytesIO(s)).size[1] for s in slices)
    assert total_h == 8100, f"拼接高度 {total_h} != 8100"
    # 倒数第二片含残片，会略超 MAX_LONG_IMAGE_HEIGHT
    h1 = Image.open(io.BytesIO(slices[0])).size[1]
    h2 = Image.open(io.BytesIO(slices[1])).size[1]
    assert h1 == 4000, f"首片应 4000，实际 {h1}"
    assert h2 == 4100, f"末片应含残片 4100，实际 {h2}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
