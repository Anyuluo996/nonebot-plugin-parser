"""Telegram 解析器测试（依赖本机 tdl 已登录且网络可达 Telegram）。

测试链接: https://t.me/anyul996/28 (频道视频帖)
若 tdl 不可用或网络不通，自动 skip。
"""

import pytest
from nonebot import logger

URL = "https://t.me/anyul996/28"
CHANNEL = "anyul996"
MSG_ID = 28


def _tdl_available() -> bool:
    """检查 tdl 是否可用（插件已初始化后调用）。"""
    try:
        from nonebot_plugin_parser.download import is_tdl_available
    except Exception:
        return False
    return is_tdl_available()


def _skip_if_no_tdl():
    """在测试体内部调用：若 tdl 不可用则 skip（避免模块级导入触发插件初始化）。"""
    if not _tdl_available():
        pytest.skip("tdl 二进制不可用（请安装 tdl 并执行 `tdl login`）")


def test_url_pattern_matches():
    """正则匹配：标准链接、话题链接可匹配，share/url 链接不匹配。"""
    _skip_if_no_tdl()
    from nonebot_plugin_parser.parsers import TelegramParser

    parser = TelegramParser()
    keyword, searched = parser.search_url(URL)
    assert keyword == "t.me"
    assert searched.group("channel") == CHANNEL
    assert searched.group("msgid") == str(MSG_ID)

    # 话题链接 t.me/channel/topic_id/msg_id
    _kw2, m2 = parser.search_url("https://t.me/mytopic123/5/28")
    assert m2.group("channel") == "mytopic123"
    assert m2.group("msgid") == "28"

    # share/url 应被排除
    from nonebot_plugin_parser.exception import ParseException

    with pytest.raises(ParseException):
        parser.search_url("https://t.me/share/url?url=abc")


@pytest.mark.asyncio
async def test_fetch_messages_returns_metadata():
    """tdl chat export 能拿到消息元数据（file/date）。"""
    _skip_if_no_tdl()
    from nonebot_plugin_parser.download import fetch_messages
    from nonebot_plugin_parser.exception import ParseException

    try:
        msg = await fetch_messages(CHANNEL, MSG_ID)
    except ParseException as e:
        pytest.skip(f"tdl chat export 失败（网络/会话问题）: {e}")

    assert msg.get("id") == MSG_ID
    assert msg.get("file"), f"消息 {MSG_ID} 未返回 file 字段: {msg}"
    logger.info(f"msg metadata: file={msg['file']!r}, date={msg.get('date')}, text={msg.get('text')!r}")


@pytest.mark.asyncio
async def test_telegram_parser_end_to_end():
    """端到端：TelegramParser 解析 t.me/anyul996/28 输出视频内容。"""
    _skip_if_no_tdl()
    from nonebot_plugin_parser.parsers import VideoContent, TelegramParser
    from nonebot_plugin_parser.exception import ParseException, DownloadException

    parser = TelegramParser()
    keyword, searched = parser.search_url(URL)
    assert searched, "无法匹配 URL"

    try:
        result = await parser.parse(keyword, searched)
    except (DownloadException, ParseException) as e:
        pytest.skip(f"tdl 下载失败（网络波动）: {e}")

    logger.info(f"result: title={result.title!r}, timestamp={result.timestamp}, contents={result.contents}")

    # 元数据断言
    assert result.author, "作者为空"
    assert f"@{CHANNEL}" in result.author.name, f"作者名应包含 @{CHANNEL}: {result.author.name}"
    assert result.timestamp, "时间戳为空"

    # 内容断言：必须解析出至少 1 个媒体内容
    assert result.contents, "未解析出任何媒体内容"
    # 消息 28 是视频文件，期望 VideoContent
    has_video = any(isinstance(c, VideoContent) for c in result.contents)
    assert has_video, f"期望解析出 VideoContent，实际: {[type(c).__name__ for c in result.contents]}"

    # 文件确实落到 cache_dir
    for cont in result.contents:
        path = await cont.get_path()
        assert path.exists(), f"文件不存在: {path}"
        logger.success(f"已下载: {path.name} ({path.stat().st_size} bytes)")


def test_whitelist_storage_roundtrip():
    """白名单增删查存盘往返。"""
    from nonebot_plugin_parser.matchers.filter import (
        add_tg_whitelist,
        get_tg_whitelist,
        is_tg_authorized,
        remove_tg_whitelist,
    )

    test_user = "test_user_12345"
    # 清理前置状态
    remove_tg_whitelist(test_user)

    assert not is_tg_authorized(test_user), f"{test_user} 不应已被授权"
    assert add_tg_whitelist(test_user) is True, "新增应返回 True"
    assert add_tg_whitelist(test_user) is False, "重复新增应返回 False"
    assert is_tg_authorized(test_user) is True
    assert test_user in get_tg_whitelist()

    assert remove_tg_whitelist(test_user) is True, "移除应返回 True"
    assert remove_tg_whitelist(test_user) is False, "重复移除应返回 False"
    assert not is_tg_authorized(test_user)
    assert test_user not in get_tg_whitelist()


def test_normalize_user_id_helper():
    """tg授权 命令的用户 id 规范化（支持 @前缀 和纯 id）。"""
    from nonebot_plugin_parser.matchers import _normalize_user_id

    assert _normalize_user_id("12345") == "12345"
    assert _normalize_user_id("@alice") == "alice"
    assert _normalize_user_id("  alice  ") == "alice"
    assert _normalize_user_id("") is None
    assert _normalize_user_id("   ") is None


# 一个最小的合成 ASCII 二维码样本（4 种 block 字符），用于纯单元测试
# 不代表真实二维码，只验证提取/渲染逻辑
_FAKE_QR_STDOUT = """WARN: If data exists in the namespace, data will be overwritten
Scan QR code with your Telegram app...
█████████████████████
██ ▄▄▄▄▄ █ ▀▀▄ █▄▄ ██
██ █   █ █▀▀ ▄▄█ ▀ ██
██ █▄▄▄█ █ ▀▀▀▀ ▀█ ██
██▄▄▄▄▄▄▄█ ▀ █▀▀▀ ██
██▄▀  ▀▄▀█▄▄▄▀ ▀▀▄██
█████████████████████
"""


def test_extract_qr_ascii_isolates_block():
    """extract_qr_ascii 能从前导行(WARN/Scan)中分离出二维码块。"""
    from nonebot_plugin_parser.download import extract_qr_ascii

    qr = extract_qr_ascii(_FAKE_QR_STDOUT)
    lines = qr.splitlines()
    # 二维码行应只含 [█▀▄ ] 字符，且每行至少含一个 block 字符
    assert lines, "提取结果为空"
    for line in lines:
        assert set(line) <= {"█", "▀", "▄", " "}, f"行含非法字符: {line!r}"
        assert any(c in "█▀▄" for c in line), f"行无 block 字符: {line!r}"
    # 不应包含 WARN / Scan 前导行
    assert "WARN" not in qr
    assert "Scan" not in qr


def test_extract_qr_ascii_handles_ansi_escape():
    """带 ANSI 转义（tdl TTY 刷新）的输出也能正确提取。"""
    from nonebot_plugin_parser.download import extract_qr_ascii

    # 模拟 tdl 在 TTY 下的 \x1b[A 光标上移刷新
    dirty = "Scan QR code...\n\x1b[A\x1b[A█████\n█▀▀▀█\n█████\n"
    qr = extract_qr_ascii(dirty)
    assert "█████" in qr, f"转义序列未被剥离: {qr!r}"
    assert "\x1b" not in qr, f"残留 ANSI 转义: {qr!r}"


def test_extract_qr_ascii_empty_returns_empty():
    """无二维码内容时返回空字符串。"""
    from nonebot_plugin_parser.download import extract_qr_ascii

    assert extract_qr_ascii("just some text\nno qr here") == ""
    assert extract_qr_ascii("") == ""


def test_extract_qr_ascii_real_capture():
    """集成：从真实 tdl 捕获的输出中提取二维码（若样本文件存在）。"""
    from pathlib import Path

    sample = Path("qrout.txt")
    if not sample.exists():
        pytest.skip("无真实 tdl 二维码样本 qrout.txt")

    from nonebot_plugin_parser.download import extract_qr_ascii

    text = sample.read_text(encoding="utf-8")
    qr = extract_qr_ascii(text)
    lines = qr.splitlines()
    # 真实二维码应至少 21 行（含 quiet zone），宽度 45 左右
    assert len(lines) >= 20, f"提取行数过少: {len(lines)}"
    for line in lines:
        assert set(line) <= {"█", "▀", "▄", " "}, f"含非法字符: {line!r}"


def test_render_qr_ascii_to_png_returns_valid_png():
    """render_qr_ascii_to_png 渲染出有效 PNG 字节（用合成样本）。"""
    from nonebot_plugin_parser.utils import render_qr_ascii_to_png
    from nonebot_plugin_parser.download import extract_qr_ascii

    ascii_qr = extract_qr_ascii(_FAKE_QR_STDOUT)
    assert ascii_qr, "前置：提取失败"

    png = render_qr_ascii_to_png(ascii_qr, scale=4, border=2)
    assert isinstance(png, bytes)
    assert len(png) > 100, f"PNG 字节过少: {len(png)}"
    # PNG 文件签名
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "非有效 PNG 签名"

    # 验证 PIL 能重新打开
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.width > 0
    assert img.height > 0


def test_render_qr_ascii_to_png_pixel_mapping():
    """验证 4 种字符到像素的正确映射（上半/下半黑）。"""
    import io

    from PIL import Image

    from nonebot_plugin_parser.utils import render_qr_ascii_to_png

    # 单字符行，覆盖 4 种情况
    ascii_qr = "█▀▄ "
    png = render_qr_ascii_to_png(ascii_qr, scale=1, border=0)
    img = Image.open(io.BytesIO(png)).convert("L")  # 0=黑 255=白
    # 宽度 = 4 字符，高度 = 2 像素行
    assert img.size == (4, 2), f"尺寸错误: {img.size}"
    pixels = img.load()
    # 期望: (字符 -> 上半像素, 下半像素)，True=黑(<128)
    expected = {
        0: ("█", True, True),
        1: ("▀", True, False),
        2: ("▄", False, True),
        3: (" ", False, False),
    }
    for col, (ch, upper_black, lower_black) in expected.items():
        assert (pixels[col, 0] < 128) is upper_black, f"字符 {ch!r} 上半像素错误"
        assert (pixels[col, 1] < 128) is lower_black, f"字符 {ch!r} 下半像素错误"


def test_tg_login_command_exists():
    """tg登录 命令已注册：matchers 模块定义了 _tg_login（由 on_command 装饰器创建）。"""
    import nonebot_plugin_parser.matchers as m

    assert hasattr(m, "_tg_login"), "matchers 模块缺少 _tg_login"
    assert hasattr(m, "_normalize_user_id"), "matchers 模块缺少 _normalize_user_id"


def test_is_tdl_available_returns_bool():
    """is_tdl_available 返回布尔值（不抛异常）。"""
    from nonebot_plugin_parser.download import is_tdl_available

    result = is_tdl_available()
    assert isinstance(result, bool)
