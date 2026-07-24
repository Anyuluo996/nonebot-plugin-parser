"""验证 NGA render_messages 的单图直发 / 多图合并转发分支。

通过 monkeypatch render_image 返回合成的长图 bytes，绕过真实浏览器渲染，
直接断言 render_messages 产出的 UniMessage 结构。
"""

import io

import pytest
from PIL import Image


def _png_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (120, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeResult:
    """最小 ParseResult 替身，仅满足 render_messages 读取的字段。"""

    display_url = "https://bbs.nga.cn/read.php?tid=1"
    repost_display_url = None


async def _async_return(val):
    return val


class _FakeBot:
    self_id = "10000"


@pytest.fixture(autouse=True)
def _set_current_bot(monkeypatch):
    """construct_forward_message 读 current_bot ContextVar，测试里注入假 bot"""
    from nonebot_plugin_parser import helper

    token = helper.current_bot.set(_FakeBot())
    yield
    helper.current_bot.reset(token)


@pytest.mark.asyncio
async def test_single_image_direct_send(monkeypatch):
    """不超高的单图：产出 1 条消息，内容为 Image（非 Reference）"""
    from nonebot_plugin_alconna.uniseg import Image as UniImage

    from nonebot_plugin_parser.renders.nga import Renderer

    renderer = Renderer()
    monkeypatch.setattr(renderer, "render_image", lambda result: _async_return(_png_bytes(720, 2000)))
    monkeypatch.setattr(type(renderer), "append_url", property(lambda self: False))

    messages = [m async for m in renderer.render_messages(_FakeResult())]
    assert len(messages) == 1
    segs = list(messages[0])
    assert len(segs) == 1
    assert isinstance(segs[0], UniImage)


@pytest.mark.asyncio
async def test_multi_image_forward(monkeypatch):
    """超高切片多图（≥2）：产出 1 条合并转发消息（Reference），含多个节点"""
    from nonebot_plugin_alconna.uniseg import Reference

    from nonebot_plugin_parser.helper import UniHelper
    from nonebot_plugin_parser.renders.nga import Renderer
    from nonebot_plugin_parser.renders.base import MAX_LONG_IMAGE_HEIGHT

    renderer = Renderer()
    monkeypatch.setattr(
        renderer, "render_image", lambda result: _async_return(_png_bytes(720, MAX_LONG_IMAGE_HEIGHT * 2 + 1500))
    )
    monkeypatch.setattr(type(renderer), "append_url", property(lambda self: False))

    messages = [m async for m in renderer.render_messages(_FakeResult())]
    assert len(messages) == 1, "多图应合并为 1 条转发消息"
    segs = list(messages[0])
    assert len(segs) == 1
    assert isinstance(segs[0], Reference), "多图应为合并转发 Reference"
    # extract_forward_nodes 展开为逐节点消息，3 片图 → 3 条（残片 1500px ≥ 800 不合并）
    assert len(UniHelper.extract_forward_nodes(messages[0])) == 3


@pytest.mark.asyncio
async def test_multi_image_forward_with_url(monkeypatch):
    """多图 + append_url：URL 作为末尾文本节点并入转发"""
    from nonebot_plugin_alconna.uniseg import Reference

    from nonebot_plugin_parser.helper import UniHelper
    from nonebot_plugin_parser.renders.nga import Renderer
    from nonebot_plugin_parser.renders.base import MAX_LONG_IMAGE_HEIGHT

    renderer = Renderer()
    monkeypatch.setattr(
        renderer, "render_image", lambda result: _async_return(_png_bytes(720, MAX_LONG_IMAGE_HEIGHT + 1500))
    )
    monkeypatch.setattr(type(renderer), "append_url", property(lambda self: True))

    messages = [m async for m in renderer.render_messages(_FakeResult())]
    assert len(messages) == 1
    segs = list(messages[0])
    assert isinstance(segs[0], Reference)
    # 2 片图（残片 1500px ≥ 800 不合并）+ 1 个 URL 文本节点 = 3 个节点
    assert len(UniHelper.extract_forward_nodes(messages[0])) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
