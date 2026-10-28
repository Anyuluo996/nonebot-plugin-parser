"""验证 _slice_segs 遵守 use_base64 配置（2026-10-28 napcat WS 断连事故回归）。

bytes 直塞 img_seg 会走 Image(raw=) 分支无条件转 base64，绕过
parser_use_base64=false 的文件路径配置——渲染长图切片 base64 后塞进单个
WS 请求，触发 napcat 硬编码的 maxPayload=50MiB 直接断连且该消息丢失。
use_base64=False 时切片必须落盘到共享缓存目录，以路径发送。

注意：nonebot_plugin_parser.* 的 import 必须放在测试函数内部，
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import io

import pytest
from PIL import Image


def _png_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (120, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_slice_segs_file_path_when_not_base64(monkeypatch):
    """use_base64=False：切片落盘为 .jpg，图片段带路径且不含 raw bytes。"""
    from pathlib import Path

    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.renders.base import ImageRenderer

    monkeypatch.setattr(pconfig, "parser_use_base64", False)
    slices = await ImageRenderer._split_long_image(_png_bytes(720, 9000))
    assert len(slices) > 1

    segs = await ImageRenderer._slice_segs(slices)
    assert len(segs) == len(slices)
    for seg, piece in zip(segs, slices):
        raw = getattr(seg, "raw", None)
        assert raw is None, "非 base64 模式不应内嵌 bytes"
        path = Path(str(getattr(seg, "path", "")))
        assert path.exists(), f"切片应已落盘: {path}"
        assert path.suffix == ".jpg", f"JPEG 切片后缀应为 .jpg: {path}"
        assert path.read_bytes() == piece, "落盘内容应与切片 bytes 一致"


@pytest.mark.asyncio
async def test_slice_segs_raw_when_base64(monkeypatch):
    """use_base64=True：切片保持 raw bytes（显式开启 base64 的部署不受影响）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.renders.base import ImageRenderer

    monkeypatch.setattr(pconfig, "parser_use_base64", True)
    slices = await ImageRenderer._split_long_image(_png_bytes(720, 9000))

    segs = await ImageRenderer._slice_segs(slices)
    for seg, piece in zip(segs, slices):
        assert getattr(seg, "raw", None) == piece, "base64 模式应内嵌原始切片 bytes"
        assert getattr(seg, "path", None) is None


@pytest.mark.asyncio
async def test_nga_single_slice_goes_file_path(monkeypatch):
    """NGA 不超高单图在 use_base64=False 下也走文件路径（bytes 分支绕过配置的回归）。"""
    from pathlib import Path

    from nonebot_plugin_alconna.uniseg import Image as UniImage

    from nonebot_plugin_parser import helper
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.renders.nga import Renderer

    monkeypatch.setattr(pconfig, "parser_use_base64", False)

    class _FakeBot:
        self_id = "10000"

    token = helper.current_bot.set(_FakeBot())
    try:
        renderer = Renderer()

        async def fake_render_image(result):
            return _png_bytes(720, 2000)

        monkeypatch.setattr(renderer, "render_image", fake_render_image)
        monkeypatch.setattr(type(renderer), "append_url", property(lambda self: False))

        class _FakeResult:
            display_url = None
            repost_display_url = None

        messages = [m async for m in renderer.render_messages(_FakeResult())]
        assert len(messages) == 1
        segs = list(messages[0])
        assert len(segs) == 1
        assert isinstance(segs[0], UniImage)
        raw = getattr(segs[0], "raw", None)
        assert raw is None, "单图也不应以 base64 进 WS"
        path = Path(str(getattr(segs[0], "path", "")))
        assert path.exists(), "单图应落盘走路径"
    finally:
        helper.current_bot.reset(token)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
