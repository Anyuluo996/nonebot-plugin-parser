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


def test_estimate_item_height_counts_newlines():
    """文字项必须按 \\n 分行估算（template 是 pre-wrap），空行也算一行。

    回归：旧模型 len(item)/_CHARS_PER_LINE 把整篇多行正文算成一两行。
    """
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer, _CHARS_PER_LINE, _LINE_HEIGHT_PX

    assert HtmlRenderer._estimate_item_height("a\nb") == 2 * _LINE_HEIGHT_PX + 4, "两个换行段落应算 2 行"
    assert HtmlRenderer._estimate_item_height("a\n\nb") == 3 * _LINE_HEIGHT_PX + 4, "空行也占 1 行"
    assert HtmlRenderer._estimate_item_height("\n") == _LINE_HEIGHT_PX + 4, "纯空行至少 1 行"
    assert HtmlRenderer._estimate_item_height("") == _LINE_HEIGHT_PX + 4, "空串至少 1 行"
    # 单行长文本仍按字符数折行
    wrapped = "x" * (_CHARS_PER_LINE * 3)
    assert HtmlRenderer._estimate_item_height(wrapped) == 3 * _LINE_HEIGHT_PX + 4
    # 多行 + 折行叠加: 2 个满宽行(86 字符) + 1 满宽行 + 1 短行 = 4 行
    mixed = "x" * (_CHARS_PER_LINE * 2) + "\n" + "y" * _CHARS_PER_LINE + "\n" + "z"
    assert HtmlRenderer._estimate_item_height(mixed) == 4 * _LINE_HEIGHT_PX + 4


def test_multiline_single_paragraph_needs_paging():
    """单个 graphics 字符串内含大量换行（酷安/Lofter/ILLU 整篇正文）必须分页。

    旧模型: 300 行短句共 899 字符 ≈ 21 折行 ≈ 546px → 不分页 → 超光栅上限尾部空白。
    """
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    text = "\n".join(["短句"] * 300)
    result = _result(graphics=[text])

    assert HtmlRenderer()._needs_paging(result), "300 行正文（≈7800px）应触发分页"


@pytest.mark.asyncio
async def test_single_huge_string_split_into_chunks():
    """单个超长字符串（无换行）也要被拆成多块，不能整篇塞进一页。"""
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    text = "x" * 200_000
    result = _result(graphics=[text])
    chunks = HtmlRenderer._chunk_graphics(result)

    assert len(chunks) >= 2, f"200000 字符应拆成多块，实际 {len(chunks)}"
    for chunk in chunks:
        est = HtmlRenderer._estimate_page_height(chunk)
        assert est <= 7000, f"块估算高度 {est}px 超安全高度（拆项后不应再溢出）"
    # 内容不丢: 硬切处补的换行去掉后应与原文一致
    assert "".join(str(item) for chunk in chunks for item in chunk).replace("\n", "") == text


def test_needs_paging_counts_repost():
    """引用转发高度计入分页判断（正文 + 引用推文一起超安全高度 → 分页）。"""
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    graphics = ["短句"] * 200  # 200 × 30px = 6000px + 300 开销 ≈ 6300px，单独不超
    repost = ParseResult(
        platform=Platform(name="twitter", display_name="推特"),
        author=Author(name="quoted"),
        text="引用推文正文" * 1700,  # ≈10200 字符 ≈ 6192px
    )
    result = _result(graphics=graphics)
    result.repost = repost

    assert HtmlRenderer._estimate_page_height(graphics) <= 7000, "正文单独不应超安全高度"
    assert HtmlRenderer()._needs_paging(result), "正文 + 引用转发超安全高度应分页"

    # 同正文不带 repost 时不分页（确认是 repost 高度触发的分页）
    assert not HtmlRenderer()._needs_paging(_result(graphics=graphics))


@pytest.mark.asyncio
async def test_repost_rendered_as_separate_last_page(monkeypatch):
    """分页时 repost 单独成末页，前面页不重复画，引用推文正文不丢。"""
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult
    from nonebot_plugin_parser.renders.htmlrender import HtmlRenderer

    repost = ParseResult(
        platform=Platform(name="twitter", display_name="推特"),
        author=Author(name="quoted"),
        title="被引用的标题",
        text="被引用的推文正文",
    )
    result = _result(graphics=_make_long_graphics(20000))
    result.repost = repost

    renderer = HtmlRenderer()
    rendered = []

    async def fake_render_image(chunk_result):
        rendered.append(chunk_result)
        return _png_bytes(800, 2000)

    monkeypatch.setattr(renderer, "render_image", fake_render_image)
    monkeypatch.setattr(type(renderer), "append_url", property(lambda self: False))

    messages = [m async for m in renderer.render_messages(result)]

    assert len(messages) == 1, "分页结果应合并为 1 条转发消息"
    assert len(rendered) >= 3, f"长文应分多页渲染，实际 {len(rendered)}"

    repost_pages = [r for r in rendered if r.repost is repost]
    assert len(repost_pages) == 1, f"引用转发应且只应出现在 1 页，实际 {len(repost_pages)}"
    assert repost_pages[0] is rendered[-1], "引用转发页应是最后一页"
    assert repost_pages[0].graphics == [], "引用转发独立成页（graphics 为空）"
    assert all(r.repost is None for r in rendered[:-1]), "前面的页不应重复画 repost"
    assert "1/" in (rendered[0].title or "") and f"{len(rendered)}/{len(rendered)}" in (rendered[-1].title or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
