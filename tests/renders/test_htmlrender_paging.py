"""验证 HtmlRenderer 超长 graphics 分页渲染（Chromium 截图丢内容根因修复）。

回归：opus 长图文（数百文字段落 + 多图）渲染成单张巨图时，Chromium 截图超出
光栅面上限（htmlrender 0.7 dpr=2 下 ~8192 CSS px）后不再绘制内容（画面留白但
布局高度保留）。HtmlRenderer 按估算页高（文字行数 + 图片限高）对超长 graphics
分页，每块单独渲染一张完整图。

注意：nonebot_plugin_parser.* 的 import 必须放在测试函数内部，确保 conftest 的
init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import io

import pytest
from PIL import Image


def _png_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (120, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _async_return(val):
    return val


def _result(graphics, title="测试标题"):
    """构造真实 ParseResult（分页逻辑用 dataclasses.replace，需 dataclass 实例）。"""
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="bilibili", display_name="哔哩哔哩"),
        author=Author(name="test"),
        title=title,
        url="https://m.bilibili.com/opus/123",
        graphics=graphics,
    )


class _FakeBot:
    self_id = "10000"


@pytest.fixture(autouse=True)
def _provide_bot():
    """construct_forward_message 读 current_bot ContextVar，测试里注入假 bot。"""
    from nonebot_plugin_parser import helper

    token = helper.current_bot.set(_FakeBot())
    yield
    helper.current_bot.reset(token)


def _make_long_graphics(n_chars: int) -> list[str]:
    """造 n_chars 字符的文字段落（每段 100 字，避免单个超长字符串）。"""
    para = "这是测试文字段落用于验证HtmlRenderer分页逻辑。" * 2  # ~46 字/段
    chunks = []
    total = 0
    while total < n_chars:
        chunks.append(para)
        total += len(para)
    return chunks


@pytest.mark.asyncio
async def test_short_graphics_no_paging(monkeypatch):
    """短内容（估算页高 ≤ 安全高度）走基类，不分页：render_image 被调 1 次。"""
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    renderer = HtmlRenderer()
    call_count = 0

    async def fake_render_image(result):
        nonlocal call_count
        call_count += 1
        return _png_bytes(800, 2000)

    monkeypatch.setattr(renderer, "render_image", fake_render_image)
    monkeypatch.setattr(type(renderer), "append_url", property(lambda self: False))

    result = _result(graphics=["短内容"])
    messages = [m async for m in renderer.render_messages(result)]
    assert call_count == 1, f"短内容应只渲染 1 次，实际 {call_count}"
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_long_graphics_paged(monkeypatch):
    """超长内容（估算页高 > 安全高度）分页：render_image 被调多次，产出 1 条合并转发。"""
    from nonebot_plugin_alconna.uniseg import Reference

    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    renderer = HtmlRenderer()
    render_count = 0

    async def fake_render_image(result):
        nonlocal render_count
        render_count += 1
        return _png_bytes(800, 2000)  # 每页短图，不触发 _split_long_image

    monkeypatch.setattr(renderer, "render_image", fake_render_image)
    monkeypatch.setattr(type(renderer), "append_url", property(lambda self: False))

    # 20000 字符 ≈ 26000px 估算高度，应分 ≥3 页（每页 ≤7000px）
    result = _result(graphics=_make_long_graphics(20000))
    messages = [m async for m in renderer.render_messages(result)]

    assert render_count >= 3, f"20000 字符应分 ≥3 页渲染，实际 {render_count} 页"
    assert len(messages) == 1, "分页结果应合并为 1 条转发消息"
    segs = list(messages[0])
    assert isinstance(segs[0], Reference), "多页应合并转发"


@pytest.mark.asyncio
async def test_image_only_graphics_paged():
    """纯图片 graphics 也计入分页（回归：旧模型只数字符，图片再多也不分页）。

    真实案例：B站 opus 3422 字 + 6 图（估高 ~8000px > 7000）未分页，单页渲染
    超出光栅面上限，尾部内容画不出来（空白尾图）。
    """
    from pathlib import Path

    from nonebot_plugin_parser.parsers import ImageContent
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    imgs = [ImageContent(Path(f"fake_{i}.jpg")) for i in range(20)]  # 20×850=17000px
    result = _result(graphics=imgs)

    assert HtmlRenderer()._needs_paging(result), "20 张图（估高 17000px）应触发分页"
    chunks = HtmlRenderer._chunk_graphics(result)
    assert len(chunks) >= 3, f"20 张图应分 ≥3 页，实际 {len(chunks)}"
    for chunk in chunks:
        assert HtmlRenderer._estimate_page_height(chunk) <= 7000 + 850, "单页估算高度应 ≤ 安全高度（允许单项粒度溢出）"


@pytest.mark.asyncio
async def test_chunk_respects_height_limit():
    """_chunk_graphics 在段落边界切分，每块估算高度 ≤ 安全高度。"""
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    # 每段 100 字符 ≈ 60px，100 段 ≈ 6000px —— 不足分页；每段 500 字符 ≈ 321px，60 段 ≈ 19200px
    paras = ["x" * 500 for _ in range(60)]
    result = _result(graphics=paras)
    chunks = HtmlRenderer._chunk_graphics(result)

    assert len(chunks) >= 2, f"19200px 应分多块，实际 {len(chunks)}"
    for chunk in chunks:
        est = HtmlRenderer._estimate_page_height(chunk)
        assert est <= 7000 + 500, f"块估算高度 {est}px 超安全高度（允许段落粒度溢出）"


@pytest.mark.asyncio
async def test_chunk_result_keeps_context():
    """分页结果的每块保留 platform/author/title，graphics 为该块内容，无 repost/contents。"""

    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    base = ParseResult(
        platform=Platform(name="bilibili", display_name="哔哩哔哩"),
        author=Author(name="test"),
        title="原标题",
        url="https://example.com",
        graphics=["x" * 100 for _ in range(100)],
    )
    chunks = HtmlRenderer._chunk_graphics(base)
    chunk_result = HtmlRenderer._make_chunk_result(base, chunks[0], 0, len(chunks))

    assert chunk_result.platform == base.platform, "保留 platform"
    assert chunk_result.author == base.author, "保留 author"
    assert "1/" in (chunk_result.title or ""), "标题含页码"
    assert chunk_result.graphics is chunks[0], "graphics 为该块"
    assert chunk_result.contents == [], "清空 contents"
    assert chunk_result.repost is None, "清空 repost"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
