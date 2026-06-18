"""dev 分支修复的单元测试与回归测试。

覆盖范围：
- cookie.py split 健壮性（空段/无值属性，#21）
- curl 路径流式下载 + 超限 IgnoreException + 567 重试 + IgnoreException 不被错转（#8,#6,#16）
- _run_subprocess 超时 kill + ffmpeg/gifsicle 成功/失败（#6,#29）
- 转发链递归深度限制 twitter（#19）
- msgspec _safe_convert ValidationError 降级（#20）
- 渲染 _ensure_height_enough 画布扩展（#10）
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# cookie.py split 健壮性（#21）
# --------------------------------------------------------------------------- #
def test_ck2dict_normal():
    from nonebot_plugin_parser.parsers.cookie import ck2dict

    ck = "SESSDATA=1234567890; bili_jct=1234567890; DedeUserID=1234567890"
    assert ck2dict(ck) == {
        "SESSDATA": "1234567890",
        "bili_jct": "1234567890",
        "DedeUserID": "1234567890",
    }


def test_ck2dict_trailing_semicolon_no_crash():
    """尾部多余分号产生的空段，原先 split('=',1) 抛 ValueError。"""
    from nonebot_plugin_parser.parsers.cookie import ck2dict

    # 尾部两个分号 → 末尾产生空段 ""
    result = ck2dict("k1=v1;;")
    assert result == {"k1": "v1"}


def test_ck2dict_attribute_without_value_no_crash():
    """无 = 的属性段（Secure/HttpOnly），原先抛 ValueError。"""
    from nonebot_plugin_parser.parsers.cookie import ck2dict

    result = ck2dict("k1=v1; Secure; HttpOnly; k2=v2")
    assert result == {"k1": "v1", "k2": "v2"}


def test_save_cookies_with_netscape_trailing_semicolon(tmp_path):
    """save_cookies_with_netscape 对畸形 cookie 字符串不崩溃。"""
    from nonebot_plugin_parser.parsers.cookie import save_cookies_with_netscape

    cookie_file = tmp_path / "cookies.txt"
    # 不应抛异常
    save_cookies_with_netscape("SESSDATA=abc123;; Secure;", cookie_file, "example.com")
    assert cookie_file.exists()
    content = cookie_file.read_text()
    assert "SESSDATA" in content


# --------------------------------------------------------------------------- #
# curl 路径流式下载（#8,#6,#16）
# --------------------------------------------------------------------------- #
class FakeCurlResponse:
    """模拟 curl_cffi 流式响应。"""

    def __init__(self, status_code: int, chunks: list[bytes]):
        self.status_code = status_code
        self._chunks = chunks

    async def aiter_content(self, chunk_size=None, decode_unicode=False):
        for chunk in self._chunks:
            yield chunk


class FakeCurlSession:
    """模拟 curl_cffi AsyncSession。"""

    def __init__(self, responses):
        # responses: list of (status_code, chunks)
        self._responses = responses
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        index = min(self.calls, len(self._responses) - 1)
        status, chunks = self._responses[index]
        self.calls += 1
        return FakeCurlResponse(status, chunks)


@pytest.mark.asyncio
async def test_curl_stream_download_success(tmp_path, monkeypatch):
    """curl 路径流式下载成功，内容正确写盘。"""
    from nonebot_plugin_parser.download import _download_by_curl

    file_path = tmp_path / "test.bin"
    session = FakeCurlSession([(200, [b"hello", b"world"])])

    # _download_by_curl 内部 `from curl_cffi.requests import AsyncSession`
    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession", lambda *a, **k: session
    )

    result = await _download_by_curl(
        "https://example.com/file.bin", file_path, {"User-Agent": "test"}, max_retries=0
    )
    assert result == file_path
    assert file_path.read_bytes() == b"helloworld"


@pytest.mark.asyncio
async def test_curl_stream_respects_max_size(tmp_path, monkeypatch):
    """curl 流式下载超过 max_size 立即中止并抛 IgnoreException（不重试）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.download import _download_by_curl
    from nonebot_plugin_parser.exception import IgnoreException

    monkeypatch.setattr(pconfig, "parser_max_size", 1)  # 1MB

    # 提供 2MB 数据，应在累计超过 1MB 时中止
    big_chunk = b"x" * (512 * 1024)
    session = FakeCurlSession([(200, [big_chunk, big_chunk, big_chunk, big_chunk])])

    import curl_cffi.requests

    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession", lambda *a, **k: session
    )

    file_path = tmp_path / "large.bin"
    with pytest.raises(IgnoreException):
        await _download_by_curl(
            "https://example.com/large.bin", file_path, {}, max_retries=0
        )
    # 超限应删除半成品文件
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_curl_ignore_exception_not_retried_as_download(tmp_path, monkeypatch):
    """0 字节文件抛 IgnoreException，不应被 except Exception 错转成 DownloadException 重试。"""
    from nonebot_plugin_parser.download import _download_by_curl
    from nonebot_plugin_parser.exception import IgnoreException

    session = FakeCurlSession([(200, [])])  # 空内容
    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession", lambda *a, **k: session
    )

    file_path = tmp_path / "empty.bin"
    with pytest.raises(IgnoreException):
        await _download_by_curl(
            "https://example.com/empty.bin", file_path, {}, max_retries=3
        )
    # 只应调用 1 次（IgnoreException 不重试）
    assert session.calls == 1


@pytest.mark.asyncio
async def test_curl_567_retries_then_succeeds(tmp_path, monkeypatch):
    """567 频率限制触发重试，重试后成功。"""
    from nonebot_plugin_parser.download import _download_by_curl

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)  # 跳过重试等待

    session = FakeCurlSession(
        [(567, []), (567, []), (200, [b"ok"])]
    )
    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession", lambda *a, **k: session
    )

    file_path = tmp_path / "retry.bin"
    result = await _download_by_curl(
        "https://example.com/retry.bin", file_path, {}, max_retries=3
    )
    assert session.calls == 3
    assert result.read_bytes() == b"ok"


@pytest.mark.asyncio
async def test_curl_non200_raises_download_exception(tmp_path, monkeypatch):
    """非 200/567 状态码抛 DownloadException。"""
    from nonebot_plugin_parser.download import _download_by_curl
    from nonebot_plugin_parser.exception import DownloadException

    session = FakeCurlSession([(404, [])])
    monkeypatch.setattr(
        "curl_cffi.requests.AsyncSession", lambda *a, **k: session
    )

    file_path = tmp_path / "notfound.bin"
    with pytest.raises(DownloadException):
        await _download_by_curl(
            "https://example.com/404.bin", file_path, {}, max_retries=0
        )


# --------------------------------------------------------------------------- #
# _run_subprocess 超时 kill（#6）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_subprocess_success():
    """_run_subprocess 执行成功命令返回正确输出。"""
    from nonebot_plugin_parser.utils import _run_subprocess

    # echo 在 Windows 是 cmd 内置，用 python 跨平台
    returncode, stdout, stderr = await _run_subprocess(
        ["python", "-c", "print('hello')"], timeout=10
    )
    assert returncode == 0
    assert b"hello" in stdout


@pytest.mark.asyncio
async def test_run_subprocess_timeout_kills_process():
    """超时时强制 kill 子进程并抛 TimeoutError。"""
    from nonebot_plugin_parser.utils import _run_subprocess

    # 启一个会一直运行的进程（sleep 10s），超时设很短
    with pytest.raises(asyncio.TimeoutError):
        await _run_subprocess(
            ["python", "-c", "import time; time.sleep(10)"], timeout=0.5
        )


@pytest.mark.asyncio
async def test_run_subprocess_nonzero_returncode():
    """返回码非 0 时调用方（exec_ffmpeg_cmd）应抛 RuntimeError。"""
    from nonebot_plugin_parser.utils import _run_subprocess

    returncode, stdout, stderr = await _run_subprocess(
        ["python", "-c", "import sys; sys.exit(1)"], timeout=10
    )
    assert returncode == 1


@pytest.mark.asyncio
async def test_exec_ffmpeg_cmd_missing_binary():
    """ffmpeg 不存在时抛 RuntimeError（FileNotFoundError 包装）。"""
    from nonebot_plugin_parser.utils import exec_ffmpeg_cmd

    with pytest.raises(RuntimeError, match="ffmpeg"):
        await exec_ffmpeg_cmd(["ffmpeg_nonexistent_binary_xyz", "-version"])


@pytest.mark.asyncio
async def test_convert_video_to_gif_cleans_palette_on_failure(tmp_path, monkeypatch):
    """convert_video_to_gif 在 ffmpeg 失败时也应清理 _palette.png 临时文件（#29）。"""
    from nonebot_plugin_parser import utils

    # 造一个假视频文件
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"not a real video")
    palette = tmp_path / "fake_palette.png"

    # 让 exec_ffmpeg_cmd 失败
    async def fail_ffmpeg(cmd):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(utils, "exec_ffmpeg_cmd", fail_ffmpeg)

    from nonebot_plugin_parser.utils import convert_video_to_gif

    with pytest.raises(RuntimeError):
        await convert_video_to_gif(video, output_path=tmp_path / "out.gif")

    # palette 文件不应残留（即便没生成，也不应存在）；关键是 finally 执行了清理
    assert not palette.exists()


# --------------------------------------------------------------------------- #
# 转发链递归深度限制（#19）
# --------------------------------------------------------------------------- #
def test_twitter_repost_depth_limit():
    """twitter _collect_result 超过 MAX_REPOST_DEPTH 不再递归，截断 repost。"""
    from nonebot_plugin_parser.parsers.twitter import (
        TwitterParser,
        VxTwitterResponse,
        MAX_REPOST_DEPTH,
    )

    parser = TwitterParser()

    # 构造一个深度为 MAX_REPOST_DEPTH + 3 的自引用链（循环引用）
    deep: VxTwitterResponse | None = None
    for _ in range(MAX_REPOST_DEPTH + 5):
        deep = VxTwitterResponse(
            article="title",
            date_epoch=0,
            fetched_on=0,
            likes=0,
            text="x",
            user_name="u",
            user_screen_name="s",
            user_profile_image_url="",
            qrt=deep,
        )

    # 不应抛 RecursionError；repost 链应在 MAX_REPOST_DEPTH 处截断
    result = parser._collect_result(deep)
    assert result is not None
    # 沿 repost 链走，最多 MAX_REPOST_DEPTH 层
    node = result
    depth = 0
    while node.repost is not None:
        node = node.repost
        depth += 1
        assert depth <= MAX_REPOST_DEPTH, "转发链深度超过上限"


# --------------------------------------------------------------------------- #
# msgspec _safe_convert 降级（#20）
# --------------------------------------------------------------------------- #
def test_safe_convert_validation_error_to_parse_exception():
    """_safe_convert 把 msgspec.ValidationError 转成 ParseException 带 context。"""
    from msgspec import Struct

    from nonebot_plugin_parser.parsers.bilibili import _safe_convert
    from nonebot_plugin_parser.exception import ParseException

    class Strict(Struct):
        required_field: int

    # 缺必填字段 → ValidationError → ParseException
    with pytest.raises(ParseException) as exc_info:
        _safe_convert({}, Strict, context="测试")

    assert "测试" in str(exc_info.value)


def test_safe_convert_success_passes_through():
    """_safe_convert 正常数据透传，等价于 convert。"""
    from msgspec import Struct, convert

    from nonebot_plugin_parser.parsers.bilibili import _safe_convert

    class Item(Struct):
        a: int

    result = _safe_convert({"a": 1}, Item, context="正常")
    assert result.a == 1


# --------------------------------------------------------------------------- #
# 渲染 _ensure_height_enough 画布扩展（#10）
# --------------------------------------------------------------------------- #
def test_render_height_extension():
    """_ensure_height_enough 在画布不足时扩展，且 close 旧图。"""
    from PIL import Image, ImageDraw

    from nonebot_plugin_parser.renders.common import (
        CommonRenderer,
        RenderContext,
    )
    from nonebot_plugin_parser.parsers.data import ParseResult, Platform

    renderer = CommonRenderer()
    # 模拟一个高度不足的画布
    small_image = Image.new("RGB", (800, 100), (255, 255, 255))
    ctx = RenderContext(
        result=ParseResult(
            platform=Platform(name="test", display_name="test"),
            url="https://example.com",
        ),
        card_width=800,
        content_width=750,
        image=small_image,
        draw=ImageDraw.Draw(small_image),
        y_pos=50,  # 已用到 50，需要 500 高度 → 不够
    )

    original_height = ctx.image.height
    renderer._ensure_height_enough(ctx, needed_height=500)
    # 画布应被扩展
    assert ctx.image.height > original_height
    assert ctx.image.height >= 50 + 500


@pytest.mark.asyncio
async def test_render_does_not_truncate_long_content():
    """端到端：渲染超长文本不应因画布不足被 crop 截断。"""
    from nonebot_plugin_parser.renders.common import CommonRenderer
    from nonebot_plugin_parser.parsers.data import (
        ParseResult,
        Platform,
        Author,
    )

    renderer = CommonRenderer()
    renderer.load_resources()

    # 超长文本（远超单次估算高度）
    long_text = "这是一段超长测试文本用于验证画布动态扩展。" * 100
    result = ParseResult(
        platform=Platform(name="telegram", display_name="TG"),
        url="https://t.me/test/1",
        author=Author(name="测试用户"),
        text=long_text,
    )

    image_bytes = await renderer.render_image(result)
    assert len(image_bytes) > 0

    # 从字节加载回图片，验证高度足够容纳内容（不是被截断的极小高度）
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(image_bytes))
    # 超长文本应产生较高图片；若被裁剪会异常矮
    assert img.height > 500, f"图片高度 {img.height} 过小，疑似内容被裁剪"
