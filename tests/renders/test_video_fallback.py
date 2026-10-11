"""VideoContent.fallback 渲染层降级回归测试。

回归 2026-10-11 yun 事故: 幻灯片合成超时后被计为「1 项媒体下载失败」, 整条
消息只剩失败文案。降级出口生效时, 合成失败应回灌原始媒体逐项直发, 不再报
失败; 无 fallback 时保持原行为(计失败 + 文案 + 抛 DownloadException)。

注意: nonebot_plugin_parser.* 的 import 必须放在测试函数内部,
确保 conftest 的 init_nonebot session fixture 先完成 nonebot 初始化与插件加载。
"""

import asyncio
from pathlib import Path

import pytest

TEST_DIR = __import__("pathlib").Path(__file__).parent.parent
RED_PNG = TEST_DIR / "_t_red.png"
GREEN_PNG = TEST_DIR / "_t_green.png"


def _result(contents=None):
    from nonebot_plugin_parser.parsers import Author, Platform, ParseResult

    return ParseResult(
        platform=Platform(name="douyin", display_name="抖音"),
        author=Author(name="test"),
        title="t",
        url="https://example.com",
        contents=contents or [],
        graphics=[],
    )


def _img(path):
    from nonebot_plugin_parser.parsers import ImageContent

    return ImageContent(path_task=path)


async def _failed_video_content() -> Path:
    """模拟合成失败: VideoContent 的下载/合成 task 抛 DownloadException。"""
    from nonebot_plugin_parser.exception import DownloadException

    raise DownloadException("幻灯片视频合成失败")


def _failed_video(fallback=None):
    from nonebot_plugin_parser.parsers import VideoContent

    task = asyncio.create_task(_failed_video_content())
    return VideoContent(task, fallback=fallback)


def _flatten(messages):
    return [seg for msg in messages for seg in msg]


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


async def _collect_all(renderer, result, out: list):
    """把 render_contents 的全部消息追加进 out; 生成器中途抛异常则原样上抛。"""
    async for m in renderer.render_contents(result):
        out.append(m)


@pytest.mark.asyncio
async def test_video_synthesis_fail_falls_back_to_images():
    """合成失败 + fallback 工厂 → 原图直发, 不产生失败文案。"""
    from nonebot_plugin_parser.renders import get_renderer

    factory_calls: list[int] = []

    def _fallback():
        factory_calls.append(1)
        return [_img(RED_PNG)]

    renderer = get_renderer("douyin")
    result = _result(contents=[_failed_video(fallback=_fallback)])
    messages: list = []
    await _collect_all(renderer, result, messages)

    assert len(factory_calls) == 1, "惰性工厂应在降级时恰好调用一次"
    segs = _flatten(messages)
    assert len(segs) == 1, f"应降级直发 1 张原图, 实际 {len(segs)}"
    assert not any(isinstance(seg, str) and "失败" in str(seg) for seg in segs), "降级成功不应再发失败文案"


@pytest.mark.asyncio
async def test_video_fail_without_fallback_counts_failure():
    """合成失败 + 无 fallback → 保持原行为: 失败文案 + DownloadException。"""
    from nonebot_plugin_parser.renders import get_renderer
    from nonebot_plugin_parser.exception import DownloadException

    renderer = get_renderer("douyin")
    result = _result(contents=[_failed_video()])
    messages: list = []
    with pytest.raises(DownloadException):
        await _collect_all(renderer, result, messages)

    assert len(messages) == 1, "应只发一条失败文案"
    text = str(messages[0])
    assert "1 项媒体下载失败" in text


@pytest.mark.asyncio
async def test_video_fallback_items_failure_still_counted():
    """降级产物自己也下载失败 → 按下载失败计数并报文案。"""
    from nonebot_plugin_parser.renders import get_renderer
    from nonebot_plugin_parser.exception import DownloadException

    async def _failed_img() -> Path:
        raise DownloadException("图片下载失败")

    def _fallback():
        return [
            _img(RED_PNG),
            _img(asyncio.create_task(_failed_img())),
        ]

    renderer = get_renderer("douyin")
    result = _result(contents=[_failed_video(fallback=_fallback)])
    messages: list = []
    with pytest.raises(DownloadException):
        await _collect_all(renderer, result, messages)

    text = str(messages[-1])
    assert "1 项媒体下载失败" in text


@pytest.mark.asyncio
async def test_video_fallback_preserves_order(monkeypatch):
    """多张降级图按原序直发, 不得因 extendleft 回灌而逆序。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.renders import get_renderer

    monkeypatch.setattr(pconfig, "parser_need_forward_contents", False)

    def _fallback():
        return [_img(RED_PNG), _img(GREEN_PNG)]

    renderer = get_renderer("douyin")
    result = _result(contents=[_failed_video(fallback=_fallback)])
    messages: list = []
    await _collect_all(renderer, result, messages)

    def _which(seg) -> str:
        """识别段对应的测试图: 路径字段或 raw 字节(use_base64 时)。"""
        path = getattr(seg, "path", None)
        if path is not None:
            return Path(str(path)).name
        raw = getattr(seg, "raw", None)
        if raw == RED_PNG.read_bytes():
            return "_t_red.png"
        if raw == GREEN_PNG.read_bytes():
            return "_t_green.png"
        return "unknown"

    segs = _flatten(messages)
    assert len(segs) == 2, f"应直发 2 张降级图, 实际 {len(segs)}"
    names = [_which(seg) for seg in segs]
    assert names == ["_t_red.png", "_t_green.png"], f"降级图应保持原序, 实际 {names}"


@pytest.mark.asyncio
async def test_video_fallback_factory_crash_counts_failure():
    """fallback 工厂自身抛非 DownloadException → 按下载失败计数, 不炸整条渲染。"""
    from nonebot_plugin_parser.renders import get_renderer
    from nonebot_plugin_parser.exception import DownloadException

    def _boom():
        raise ValueError("工厂炸了")

    renderer = get_renderer("douyin")
    result = _result(contents=[_failed_video(fallback=_boom)])
    messages: list = []
    with pytest.raises(DownloadException):
        await _collect_all(renderer, result, messages)

    assert len(messages) == 1, "工厂异常应只发一条失败文案"
    assert "1 项媒体下载失败" in str(messages[0])
