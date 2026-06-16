"""合并转发兼容性回归测试。

回归1: 单个内容(图片或视频)不应合并转发, 直接发送, 提升协议端兼容性。
回归2: extract_forward_nodes 能把合并转发消息拆成节点内容列表, 用于发送失败时降级。

注意: nonebot_plugin_parser.* 的 import 必须放在测试函数内部,
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import pytest

TEST_DIR = __import__("pathlib").Path(__file__).parent.parent
RED_PNG = TEST_DIR / "_t_red.png"
GREEN_PNG = TEST_DIR / "_t_green.png"
VIDEO_MP4 = TEST_DIR / "_t_video.mp4"


def _img(path):
    from nonebot_plugin_parser.parsers import ImageContent

    return ImageContent(path_task=path)


def _video(path):
    from nonebot_plugin_parser.parsers import VideoContent

    return VideoContent(path_task=path)


def _dynamic(path):
    from nonebot_plugin_parser.parsers import DynamicContent

    return DynamicContent(path_task=path)


def _result(contents=None, graphics=None):
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="douyin", display_name="抖音"),
        author=Author(name="test"),
        title="t",
        url="https://example.com",
        contents=contents or [],
        graphics=graphics or [],
    )


class _FakeBot:
    """最小 bot, 提供 construct_forward_message 需要的 self_id。"""

    self_id = "10000"


@pytest.fixture(autouse=True)
def _provide_bot():
    """为需要 current_bot 上下文的测试注入假 bot。"""
    from nonebot.matcher import current_bot

    token = current_bot.set(_FakeBot())
    yield
    current_bot.reset(token)


def _flatten(segments):
    """收集 render_contents 生成的所有 UniMessage 的所有 Segment。"""
    return [seg for msg in segments for seg in msg]


@pytest.mark.asyncio
async def test_single_image_no_forward():
    """单个图片: 直接发送 Image, 不生成 Reference。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("douyin")
    result = _result(contents=[_img(RED_PNG)])
    messages = [m async for m in renderer.render_contents(result)]

    segs = _flatten(messages)
    assert len(messages) == 1, f"单图应只生成 1 条消息, 实际 {len(messages)}"
    assert not any(
        type(seg).__name__ == "Reference" for seg in segs
    ), "单图不应合并转发"
    assert len(segs) == 1, f"单图消息应只含 1 个段, 实际 {len(segs)}"


@pytest.mark.asyncio
async def test_single_video_no_forward():
    """单个视频: 直接发送 Video, 不生成 Reference。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("douyin")
    result = _result(contents=[_video(VIDEO_MP4)])
    messages = [m async for m in renderer.render_contents(result)]

    segs = _flatten(messages)
    assert len(messages) == 1, f"单视频应只生成 1 条消息, 实际 {len(messages)}"
    assert not any(
        type(seg).__name__ == "Reference" for seg in segs
    ), "单视频不应合并转发"


@pytest.mark.asyncio
async def test_single_dynamic_no_forward():
    """单个实况照片(动态内容): 直接发送, 不生成 Reference。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("douyin")
    result = _result(contents=[_dynamic(VIDEO_MP4)])
    messages = [m async for m in renderer.render_contents(result)]

    segs = _flatten(messages)
    assert len(messages) == 1, f"单实况应只生成 1 条消息, 实际 {len(messages)}"
    assert not any(
        type(seg).__name__ == "Reference" for seg in segs
    ), "单实况不应合并转发"


@pytest.mark.asyncio
async def test_multiple_images_uses_forward():
    """多张图片: 合并转发 (生成 Reference)。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("douyin")
    result = _result(contents=[_img(RED_PNG), _img(GREEN_PNG)])
    messages = [m async for m in renderer.render_contents(result)]

    assert len(messages) == 1, f"多图应生成 1 条合并转发, 实际 {len(messages)}"
    segs = _flatten(messages)
    refs = [seg for seg in segs if type(seg).__name__ == "Reference"]
    assert refs, "多图应合并转发"
    # alconna 的 Reference.nodes 是 InitVar(非实例属性, 为 None),
    # 真实节点在 _children
    ref_nodes = getattr(refs[0], "_children", None) or []
    assert len(ref_nodes) == 2, f"合并转发应有 2 个节点, 实际 {len(ref_nodes)}"


@pytest.mark.asyncio
async def test_multiple_dynamics_uses_forward():
    """多个实况照片: 合并转发。"""
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("douyin")
    result = _result(contents=[_dynamic(VIDEO_MP4), _dynamic(VIDEO_MP4)])
    messages = [m async for m in renderer.render_contents(result)]

    segs = _flatten(messages)
    assert any(
        type(seg).__name__ == "Reference" for seg in segs
    ), "多实况应合并转发"


def test_extract_forward_nodes_flattens_reference():
    """extract_forward_nodes 把合并转发拆成节点内容列表 (降级用)。"""
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_parser.helper import UniHelper

    img1 = UniHelper.img_seg(RED_PNG)
    img2 = UniHelper.img_seg(GREEN_PNG)
    ref = UniHelper.construct_forward_message([img1, img2], user_id="123")
    msg = UniMessage(ref)

    nodes = UniHelper.extract_forward_nodes(msg)
    assert len(nodes) == 2, f"应拆出 2 个节点, 实际 {len(nodes)}"
    for node in nodes:
        assert hasattr(node, "__iter__"), "每个节点内容应为可迭代消息"


def test_extract_forward_nodes_passes_non_reference():
    """非 Reference 消息原样返回, 不丢失段。"""
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_parser.helper import UniHelper

    img = UniHelper.img_seg(RED_PNG)
    msg = UniMessage(img)

    nodes = UniHelper.extract_forward_nodes(msg)
    assert len(nodes) == 1
    assert len(list(nodes[0])) == 1, "段应保留"


def test_extract_forward_nodes_single_node_reference():
    """单节点 Reference 拆出 1 条 (发送降级时 len<=1 会跳过重发)。"""
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_parser.helper import UniHelper

    img = UniHelper.img_seg(RED_PNG)
    ref = UniHelper.construct_forward_message([img], user_id="123")
    msg = UniMessage(ref)

    nodes = UniHelper.extract_forward_nodes(msg)
    assert len(nodes) == 1
