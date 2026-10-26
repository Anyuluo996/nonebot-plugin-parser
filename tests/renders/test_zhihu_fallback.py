"""验证知乎渲染器非问题页的委托回退。

回归：`render_type=default` 时全局渲染器是 DefaultRenderer（纯文本，没有
`render_image`），知乎文章/单回答页委托它会 AttributeError，所有非问题页链接必崩。
修法：`getattr(g, "render_image", None)`，缺失则回退 CommonRenderer。

注意：nonebot_plugin_parser.* 的 import 放在测试函数内部，确保 conftest 的
init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import pytest


def _result(graphics=None, answers=None):
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="zhihu", display_name="知乎"),
        author=Author(name="test"),
        title="知乎标题",
        url="https://zhuanlan.zhihu.com/p/1",
        graphics=graphics or [],
        extra={"answers": answers} if answers else {},
    )


class _PlainRenderer:
    """模拟 DefaultRenderer: 纯文本渲染器，没有 render_image。"""


@pytest.mark.asyncio
async def test_non_question_page_falls_back_to_common_renderer(monkeypatch):
    """全局渲染器没有 render_image 时回退 CommonRenderer，不再 AttributeError。"""
    import nonebot_plugin_parser.renders as renders
    from nonebot_plugin_parser.renders.common import CommonRenderer
    from nonebot_plugin_parser.renders.zhihu import Renderer

    called = []

    async def fake_render_image(self, result):
        called.append((self, result))
        return b"fake-png"

    monkeypatch.setattr(renders, "get_global_renderer", lambda: _PlainRenderer())
    monkeypatch.setattr(CommonRenderer, "render_image", fake_render_image)

    result = _result(graphics=["知乎文章正文"])
    assert await Renderer().render_image(result) == b"fake-png"
    assert len(called) == 1 and called[0][1] is result, "应调用 CommonRenderer.render_image(result)"
    assert isinstance(called[0][0], CommonRenderer), "回退实例应为 CommonRenderer"


@pytest.mark.asyncio
async def test_non_question_page_prefers_global_render_image(monkeypatch):
    """全局渲染器自带 render_image 时仍优先委托它（htmlrender/common 模式行为不变）。"""
    import nonebot_plugin_parser.renders as renders
    from nonebot_plugin_parser.renders.zhihu import Renderer

    delegated = []

    class _ImageRenderer:
        async def render_image(self, result):
            delegated.append(result)
            return b"global-png"

    monkeypatch.setattr(renders, "get_global_renderer", lambda: _ImageRenderer())

    result = _result(graphics=["知乎文章正文"])
    assert await Renderer().render_image(result) == b"global-png"
    assert delegated == [result]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
