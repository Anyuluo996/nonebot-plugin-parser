"""合并转发分批回归测试（2026-10-28 napcat WS 断连事故）。

单个合并转发请求携带全部节点时，大媒体 base64 会撞 napcat 硬编码的
maxPayload=50MiB，超限直接断连且该消息丢失。节点按 FORWARD_NODE_BATCH_SIZE
拆成多张卡片分别发送，每张独立重试/降级，失败重发代价小。

注意：nonebot_plugin_parser.* 的 import 必须放在测试函数内部，
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import pytest

TEST_DIR = __import__("pathlib").Path(__file__).parent.parent
RED_PNG = TEST_DIR / "_t_red.png"


def _img(path):
    from nonebot_plugin_parser.parsers import ImageContent

    return ImageContent(path_task=path)


class _FakeBot:
    """提供 construct_forward_message 需要的 self_id。"""

    self_id = "10000"


@pytest.fixture(autouse=True)
def _provide_bot():
    from nonebot.matcher import current_bot

    token = current_bot.set(_FakeBot())
    yield
    current_bot.reset(token)


def _ref_nodes(ref) -> int:
    """alconna 的 Reference.nodes 是 InitVar，真实节点在 _children。"""
    return len(getattr(ref, "_children", None) or [])


def test_construct_forward_messages_batches():
    """25 个节点按批大小 10 拆成 3 张卡片（10/10/5），顺序保持。"""
    from nonebot_plugin_parser.helper import FORWARD_NODE_BATCH_SIZE, UniHelper

    assert FORWARD_NODE_BATCH_SIZE == 10
    segs = [UniHelper.img_seg(RED_PNG) for _ in range(25)]
    refs = UniHelper.construct_forward_messages(segs, user_id="123")

    assert [(_ref_nodes(r)) for r in refs] == [10, 10, 5]


def test_construct_forward_messages_single_batch_when_under_limit():
    """不超过批大小时行为与不分批一致：单张卡片。"""
    from nonebot_plugin_alconna.uniseg import UniMessage

    from nonebot_plugin_parser.helper import UniHelper

    segs = [UniHelper.img_seg(RED_PNG) for _ in range(3)]
    refs = UniHelper.construct_forward_messages(segs, user_id="123")
    assert len(refs) == 1
    assert _ref_nodes(refs[0]) == 3
    nodes = UniHelper.extract_forward_nodes(UniMessage(refs[0]))
    assert len(nodes) == 3


def test_construct_forward_messages_no_batch():
    """batch_size<=0 时不分批，全部节点进单张卡片。"""
    from nonebot_plugin_parser.helper import UniHelper

    segs = [UniHelper.img_seg(RED_PNG) for _ in range(25)]
    refs = UniHelper.construct_forward_messages(segs, batch_size=0, user_id="123")
    assert len(refs) == 1
    assert _ref_nodes(refs[0]) == 25


@pytest.mark.asyncio
async def test_render_contents_batches_forward():
    """12 张图片合并转发拆成 2 条消息（10+2），每条独立发送/重试。"""
    from nonebot_plugin_parser.helper import UniHelper
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult
    from nonebot_plugin_parser.renders import get_renderer

    renderer = get_renderer("douyin")
    result = ParseResult(
        platform=Platform(name="douyin", display_name="抖音"),
        author=Author(name="test"),
        title="t",
        url="https://example.com",
        contents=[_img(RED_PNG) for _ in range(12)],
        graphics=[],
    )
    messages = [m async for m in renderer.render_contents(result)]

    assert len(messages) == 2, f"12 节点应拆 2 条转发消息, 实际 {len(messages)}"
    counts = [len(UniHelper.extract_forward_nodes(m)) for m in messages]
    assert counts == [10, 2], f"节点数应为 [10, 2], 实际 {counts}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
