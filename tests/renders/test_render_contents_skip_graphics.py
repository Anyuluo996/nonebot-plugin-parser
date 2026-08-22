"""验证 ImageRenderer.render_contents 跳过 graphics（opus 刷屏根因回归测试）。

回归：opus 长图文把正文文字段落（str）和内嵌图（ImageContent）塞进 result.graphics，
被 CommonRenderer 渲染进卡片图后，基类 render_contents 又把它们当独立媒体二次发出。
ImageRenderer 覆盖 render_contents 后应跳过 graphics，只发独立的 contents 媒体。

注意：nonebot_plugin_parser.* 的 import 必须放在测试函数内部，确保 conftest 的
init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import pytest

TEST_DIR = __import__("pathlib").Path(__file__).parent.parent
RED_PNG = TEST_DIR / "_t_red.png"
GREEN_PNG = TEST_DIR / "_t_green.png"


def _img(path):
    from nonebot_plugin_parser.parsers import ImageContent

    return ImageContent(path_task=path)


def _result(contents=None, graphics=None):
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="bilibili", display_name="哔哩哔哩"),
        author=Author(name="test"),
        title="t",
        url="https://example.com",
        contents=contents or [],
        graphics=graphics or [],
    )


class _FakeBot:
    """最小 bot，提供 construct_forward_message 需要的 self_id。"""

    self_id = "10000"


@pytest.fixture(autouse=True)
def _provide_bot():
    """为需要 current_bot 上下文的测试注入假 bot。"""
    from nonebot.matcher import current_bot

    token = current_bot.set(_FakeBot())
    yield
    current_bot.reset(token)


@pytest.mark.asyncio
async def test_graphics_skipped_by_image_renderer():
    """graphics 含 str 段落 + ImageContent 图片：render_contents 不产生任何消息。

    这是 opus 刷屏的核心回归点 —— 397 个文字段落不应再被当作媒体发出。
    """
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("bilibili")
    result = _result(
        graphics=[
            "第一段正文文字",
            "第二段正文文字",
            _img(RED_PNG),
            "第三段正文文字",
            _img(GREEN_PNG),
        ]
    )
    messages = [m async for m in renderer.render_contents(result)]
    assert messages == [], f"graphics 应被跳过，实际产生 {len(messages)} 条消息"


@pytest.mark.asyncio
async def test_contents_still_sent_by_image_renderer():
    """独立的 contents（图片）仍正常发出，不受 graphics 跳过影响。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("bilibili")
    result = _result(
        contents=[_img(RED_PNG), _img(GREEN_PNG)],
        graphics=["这段文字应被跳过"],
    )
    messages = [m async for m in renderer.render_contents(result)]
    assert len(messages) == 1, f"多图 contents 应合并为 1 条转发，实际 {len(messages)}"
    # graphics 的文字不应出现在产出的消息里
    from nonebot_plugin_alconna.uniseg import Reference

    segs = [seg for msg in messages for seg in msg]
    assert any(isinstance(seg, Reference) for seg in segs), "多图应合并转发"


@pytest.mark.asyncio
async def test_graphics_restored_after_render_contents():
    """render_contents 临时清空 graphics 后必须还原，不能污染解析结果缓存。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("bilibili")
    original_graphics = ["文字A", "文字B", _img(RED_PNG)]
    result = _result(graphics=original_graphics)

    # 消耗 render_contents（会临时清空再还原 graphics）
    _ = [m async for m in renderer.render_contents(result)]

    assert result.graphics is original_graphics, "graphics 引用应还原"
    assert list(result.graphics) == list(original_graphics), "graphics 内容应还原"


@pytest.mark.asyncio
async def test_graphics_video_sent_but_text_images_skipped():
    """graphics 里的 VideoContent（小黑盒文章内嵌视频）单独发出，文字/图片仍跳过。

    回归：卡片只画视频封面，若 render_contents 把 graphics 一刀切跳过，
    文章内嵌视频会凭空丢失。
    """
    from nonebot_plugin_alconna.uniseg import Video as VideoSeg

    from nonebot_plugin_parser.parsers import VideoContent
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("heybox")
    video = VideoContent(path_task=TEST_DIR / "_t_video.mp4")
    result = _result(graphics=["文字段落应被跳过", _img(RED_PNG), video])

    messages = [m async for m in renderer.render_contents(result)]

    assert len(messages) == 1, f"单个视频应直发 1 条消息，实际 {len(messages)}"
    segs = list(messages[0])
    assert any(isinstance(seg, VideoSeg) for seg in segs), "消息应含视频段"
    assert video in list(result.graphics), "graphics 应还原"
    assert result.contents == [], "contents 应还原"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
