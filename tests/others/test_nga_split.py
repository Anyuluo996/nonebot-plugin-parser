"""验证长图切片逻辑（纯 Pillow，但需 lazy import 触发插件初始化）。

切片方法原为 NGA 私有实现，现已提升到基类 ImageRenderer._split_long_image，
供所有渲染器（NGA/贴吧/知乎/B站等）复用。这里直接测基类。

相邻切片保留 SLICE_OVERLAP_PX 重叠：边界切断文字行时下一片开头带上
上一片末尾内容，阅读不断行。
"""

import io

import pytest
from PIL import Image

MAX_LONG_IMAGE_HEIGHT = 4000
SLICE_OVERLAP_PX = 160


def _make_long_png(width: int, height: int) -> bytes:
    """造一张带横线标记的长图 PNG bytes"""
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    for y in range(0, height, 1000):
        for x in range(width):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_gradient_png(width: int, height: int) -> bytes:
    """按 64px 实色横带编码绝对行号（带号 b=y//64 → R=b），用于直接读出各切片的绝对位置。

    用 64px 实色带而非逐行像素：切片以 JPEG 有损编码，逐行渐变在解压后有
    ±个位数误差，带内实色则 DC 系数几乎无损，带号解码可精确还原行号。
    """
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        rgb = ((y // 64) % 256, 0, 0)
        for x in range(width):
            px[x, y] = rgb
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_BAND_PX = 64


def _slice_tops(slices: list[bytes]) -> list[int]:
    """由每片首行像素解出其在原图中的绝对 y（实色带编码，JPEG 误差 << 半带宽）"""
    tops: list[int] = []
    for s in slices:
        img = Image.open(io.BytesIO(s))
        r, _, _ = img.getpixel((0, 0))
        tops.append(r * _BAND_PX)
    return tops


@pytest.mark.asyncio
async def test_split_long_image():
    """超高长图应被正确切片（含重叠），覆盖全图且每片不超高"""
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_gradient_png(720, 9500)
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) == 3, f"9500/{MAX_LONG_IMAGE_HEIGHT} 应切 3 片, 实际 {len(slices)}"
    tops = _slice_tops(slices)
    heights = [Image.open(io.BytesIO(s)).size[1] for s in slices]
    for i, h in enumerate(heights):
        assert h <= MAX_LONG_IMAGE_HEIGHT, f"片{i} 超高: {h}"
    # 覆盖全图：首片从 0 开始，末片到达底部
    assert tops[0] == 0, f"首片应从 0 开始, 实际 {tops[0]}"
    assert tops[-1] + heights[-1] == 9500, f"末片应到 9500, 实际 {tops[-1]}+{heights[-1]}"
    # 相邻片重叠 = SLICE_OVERLAP_PX：下一片起点 = 上一片终点 - overlap
    for i in range(1, len(tops)):
        prev_bottom = tops[i - 1] + heights[i - 1]
        got = prev_bottom - tops[i]
        assert got == SLICE_OVERLAP_PX, f"片{i - 1}→{i} 重叠应为 {SLICE_OVERLAP_PX}, 实际 {got}"


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
    （带重叠时步进 3840：边界 [0-4000, 3840-7840, 7680-8100(420px 残片)]
    → 末片并入 → [0-4000, 3840-8100]。）
    """
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_gradient_png(720, 8100)  # 残片 420px < 800 阈值
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) == 2, f"矮残片应并入，期望 2 片，实际 {len(slices)}"
    tops = _slice_tops(slices)
    heights = [Image.open(io.BytesIO(s)).size[1] for s in slices]
    assert tops == [0, 4000 - SLICE_OVERLAP_PX], f"片起点应为 [0, 3840], 实际 {tops}"
    assert heights == [4000, 8100 - 3840], f"片高应为 [4000, 4260], 实际 {heights}"


@pytest.mark.asyncio
async def test_split_pieces_are_jpeg():
    """切片以 JPEG 编码（照片型内容体积远小于 PNG）；不超高时原样透传不重编码。"""
    from nonebot_plugin_parser.renders.base import ImageRenderer

    raw = _make_long_png(720, 9000)
    slices = await ImageRenderer._split_long_image(raw)
    assert len(slices) > 1
    for s in slices:
        assert s[:2] == b"\xff\xd8", "切片应为 JPEG magic"
        with Image.open(io.BytesIO(s)) as img:
            assert img.format == "JPEG"


@pytest.mark.asyncio
async def test_split_pieces_smaller_than_png():
    """照片型内容切片 JPEG 体积应显著小于同内容 PNG（#3 目标：砍掉大头上传量）。"""
    import os

    from nonebot_plugin_parser.renders.base import ImageRenderer

    # 逐像素随机噪声：PNG 几乎压不动（实色带/规则纹理 PNG 反而极小，不代表照片）
    raw_img = Image.frombytes("RGB", (720, 9000), os.urandom(720 * 9000 * 3))
    buf = io.BytesIO()
    raw_img.save(buf, format="PNG")
    raw = buf.getvalue()

    slices = await ImageRenderer._split_long_image(raw)
    jpeg_total = sum(len(s) for s in slices)
    assert jpeg_total < len(raw) * 0.5, f"JPEG 切片总量 {jpeg_total} 应 < PNG 的一半 {len(raw)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
