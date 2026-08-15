"""解析层即时重试 (parse_with_retry) 的行为测试。

与 L2 后台重试 (failure_retry) 是两层机制，这里只验证"用户消息上下文内"的
即时重试：偶发失败重试后成功、全部尝试失败才上抛、语义性异常/超时/Telegram
不重试。stub parser 在测试函数内定义——NoneBot 初始化发生在 collect 之后，
模块顶层 import 插件包会失败（见 conftest init_nonebot fixture）。
"""

import re
import asyncio
from typing import ClassVar

import pytest


def _make_stub(platform_name: str = "douyin"):
    """构造可编程失败的 stub parser（_abstract_parser=True 不污染 _registry）。"""
    from nonebot_plugin_parser.exception import ParseException
    from nonebot_plugin_parser.parsers.base import Platform, BaseParser, PlatformEnum, handle

    enum_member = PlatformEnum(platform_name)

    class StubParser(BaseParser):
        _abstract_parser = True

        platform: ClassVar[Platform] = Platform(name=enum_member, display_name="测试")

        calls: int = 0
        fail_times: int = 0  # 前 N 次抛 ParseException（999 = 永远失败）
        exc: Exception | None = None  # 非 None 时优先抛该异常
        hang: float = 0.0  # >0 时每次解析先 sleep 该秒数（制造超时）

        @handle("stub", r"stub\.example/\w+")
        async def _parse(self, searched: re.Match[str]):
            self.calls += 1
            if self.hang:
                await asyncio.sleep(self.hang)
            if self.exc is not None:
                raise self.exc
            if self.calls <= self.fail_times:
                raise ParseException("偶发风控")
            return self.result(
                title="ok",
                author=self.create_author("stub"),
                contents=[],
                timestamp=0,
            )

    return StubParser()


def _setup(monkeypatch, retry_max: int, timeout: int | None = None):
    """统一重试参数（delay=0 让退避不耗时），返回 pconfig 供断言读取。"""
    from nonebot_plugin_parser.config import pconfig

    monkeypatch.setattr(pconfig, "parser_parse_retry_max", retry_max)
    monkeypatch.setattr(pconfig, "parser_parse_retry_delay", 0.0)
    if timeout is not None:
        monkeypatch.setattr(pconfig, "parser_parse_timeout", timeout)
    return pconfig


async def _run(parser):
    from nonebot_plugin_parser.parse_retry import parse_with_retry

    keyword, searched = parser.search_url("https://stub.example/abc")
    return await parse_with_retry(parser, keyword, searched)


@pytest.mark.asyncio
async def test_flaky_parse_retries_then_succeeds(monkeypatch):
    """偶发失败（前 2 次 ParseException）在重试预算内成功，不向用户报错。"""
    _setup(monkeypatch, retry_max=2)
    parser = _make_stub()
    parser.fail_times = 2

    result = await _run(parser)

    assert parser.calls == 3  # 1 次原始 + 2 次重试
    assert result.title == "ok"


@pytest.mark.asyncio
async def test_all_attempts_fail_raises_last_error(monkeypatch):
    """重试预算耗尽后原样上抛最后一次异常（交由 parser_handler 报错 + L2 兜底）。"""
    from nonebot_plugin_parser.exception import ParseException

    _setup(monkeypatch, retry_max=2)
    parser = _make_stub()
    parser.fail_times = 999

    with pytest.raises(ParseException, match="偶发风控"):
        await _run(parser)

    assert parser.calls == 3  # 1 次原始 + 2 次重试，不超额


@pytest.mark.asyncio
async def test_retry_disabled_by_zero(monkeypatch):
    """parse_retry_max=0 关闭重试，行为退回单次尝试。"""
    from nonebot_plugin_parser.exception import ParseException

    _setup(monkeypatch, retry_max=0)
    parser = _make_stub()
    parser.fail_times = 1

    with pytest.raises(ParseException, match="偶发风控"):
        await _run(parser)

    assert parser.calls == 1


@pytest.mark.asyncio
async def test_tip_exception_not_retried(monkeypatch):
    """TipException 是语义性提示（如无权限），重试无意义。"""
    from nonebot_plugin_parser.exception import TipException

    _setup(monkeypatch, retry_max=2)
    parser = _make_stub()
    parser.exc = TipException("无 Telegram 解析权限")

    with pytest.raises(TipException):
        await _run(parser)

    assert parser.calls == 1


@pytest.mark.asyncio
async def test_ignore_exception_not_retried(monkeypatch):
    """IgnoreException 是主动忽略，重试无意义。"""
    from nonebot_plugin_parser.exception import IgnoreException

    _setup(monkeypatch, retry_max=2)
    parser = _make_stub()
    parser.exc = IgnoreException()

    with pytest.raises(IgnoreException):
        await _run(parser)

    assert parser.calls == 1


@pytest.mark.asyncio
async def test_timeout_not_retried(monkeypatch):
    """顶层超时不重试：挂起型 parser 重试大概率同样挂起，且用户已等满预算。"""
    _setup(monkeypatch, retry_max=2, timeout=1)
    parser = _make_stub()
    parser.hang = 10

    with pytest.raises(asyncio.TimeoutError):
        await _run(parser)

    assert parser.calls == 1


@pytest.mark.asyncio
async def test_telegram_exempt_from_retry(monkeypatch):
    """Telegram 解析阶段含媒体同步下载，失败重试=整段重下，豁免即时重试。"""
    from nonebot_plugin_parser.exception import ParseException

    _setup(monkeypatch, retry_max=2)
    parser = _make_stub("telegram")
    parser.fail_times = 999

    with pytest.raises(ParseException):
        await _run(parser)

    assert parser.calls == 1
