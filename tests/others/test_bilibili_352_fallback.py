"""B 站 -352 风控回退行为的离线单元测试。

web-dynamic/v1/detail 与 opus/detail 是两个独立端点，B 站对二者风控采样互不
联动：detail 被拦（-352）时回退 opus/detail。这里 stub 掉凭据与网络调用，验证
回退触发条件与传播语义，不依赖真实 B 站接口。
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest


def _make_parser():
    """构造匿名凭据（无网络校验）的 BilibiliParser。"""
    from nonebot_plugin_parser.parsers import BilibiliParser
    from nonebot_plugin_parser.parsers.bilibili import BilibiliCredentialManager

    parser = BilibiliParser()
    parser._credentials = cast(BilibiliCredentialManager, SimpleNamespace(get=AsyncMock(return_value=None)))
    return parser


@pytest.mark.asyncio
async def test_detail_352_falls_back_to_opus(monkeypatch):
    """detail 接口 -352 时回退 parse_opus_by_id（走 opus/detail 端点）。"""
    from bilibili_api.dynamic import Dynamic
    from bilibili_api.exceptions import ResponseCodeException

    from nonebot_plugin_parser.parsers import BilibiliParser

    parser = _make_parser()
    sentinel = object()
    fallback = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(BilibiliParser, "parse_opus_by_id", fallback)
    monkeypatch.setattr(Dynamic, "is_article", AsyncMock(side_effect=ResponseCodeException(-352, "-352")))

    result = await parser.parse_dynamic_or_opus(12345)

    assert result is sentinel
    fallback.assert_awaited_once_with(12345)


@pytest.mark.asyncio
async def test_other_code_raises_without_fallback(monkeypatch):
    """非 -352 错误码（如接口雪崩 -490）原样上抛，不触发回退。"""
    from bilibili_api.dynamic import Dynamic
    from bilibili_api.exceptions import ResponseCodeException

    from nonebot_plugin_parser.parsers import BilibiliParser

    parser = _make_parser()
    fallback = AsyncMock()
    monkeypatch.setattr(BilibiliParser, "parse_opus_by_id", fallback)
    monkeypatch.setattr(Dynamic, "is_article", AsyncMock(side_effect=ResponseCodeException(-490, "else")))

    with pytest.raises(ResponseCodeException) as exc_info:
        await parser.parse_dynamic_or_opus(1)

    assert exc_info.value.code == -490
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_flow_untouched(monkeypatch):
    """detail 正常（非专栏）时行为与改动前一致，不触发回退。"""
    from bilibili_api.dynamic import Dynamic

    from nonebot_plugin_parser.parsers import BilibiliParser

    parser = _make_parser()
    fallback = AsyncMock()
    monkeypatch.setattr(BilibiliParser, "parse_opus_by_id", fallback)
    monkeypatch.setattr(Dynamic, "is_article", AsyncMock(return_value=False))

    parse_info = AsyncMock(return_value="dynamic-result")
    monkeypatch.setattr(parser, "_parse_dynamic_info", parse_info)
    # safe_convert 是同步函数，直接 patch 模块符号
    import nonebot_plugin_parser.parsers.bilibili as bili_mod

    monkeypatch.setattr(bili_mod, "safe_convert", lambda info, *a, **k: SimpleNamespace(item="info"))
    # dynamic.get_info 走缓存（is_article 已填充），无需真实请求
    monkeypatch.setattr(Dynamic, "get_info", AsyncMock(return_value={}))

    result = await parser.parse_dynamic_or_opus(42)

    assert result == "dynamic-result"
    fallback.assert_not_awaited()
    parse_info.assert_awaited_once_with("info")


@pytest.mark.asyncio
async def test_fallback_endpoint_352_propagates(monkeypatch):
    """回退端点 opus/detail 也被 -352 拦时原样上抛，交由 L2 队列按分钟节奏重试。"""
    from bilibili_api.dynamic import Dynamic
    from bilibili_api.exceptions import ResponseCodeException

    from nonebot_plugin_parser.parsers import BilibiliParser

    parser = _make_parser()
    fallback = AsyncMock(side_effect=ResponseCodeException(-352, "-352"))
    monkeypatch.setattr(BilibiliParser, "parse_opus_by_id", fallback)
    monkeypatch.setattr(Dynamic, "is_article", AsyncMock(side_effect=ResponseCodeException(-352, "-352")))

    with pytest.raises(ResponseCodeException) as exc_info:
        await parser.parse_dynamic_or_opus(1)

    assert exc_info.value.code == -352
    fallback.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_bili_ticket_wrapper_degrades_on_failure(monkeypatch):
    """票据获取失败时降级为无票据直连并冷却，不放大为全部 B 站请求失败。"""
    import nonebot_plugin_parser.parsers.bilibili as bili_mod

    monkeypatch.setattr(bili_mod, "_bili_ticket_fail_until", 0.0)
    monkeypatch.setattr(bili_mod, "_orig_get_bili_ticket", AsyncMock(side_effect=RuntimeError("ticket endpoint down")))

    ticket, expires = await bili_mod._safe_get_bili_ticket()

    assert (ticket, expires) == ("", "0")  # 降级：不带票据继续请求
    assert bili_mod._bili_ticket_fail_until > 0.0  # 进入冷却期


@pytest.mark.asyncio
async def test_bili_ticket_wrapper_cooldown_skips_retry(monkeypatch):
    """冷却期内不再尝试请求票据端点，避免每个请求都白等一次失败。"""
    import time as time_mod

    import nonebot_plugin_parser.parsers.bilibili as bili_mod

    monkeypatch.setattr(bili_mod, "_bili_ticket_fail_until", time_mod.monotonic() + 600)
    orig = AsyncMock(return_value=("t", "1"))
    monkeypatch.setattr(bili_mod, "_orig_get_bili_ticket", orig)

    ticket, expires = await bili_mod._safe_get_bili_ticket()

    assert (ticket, expires) == ("", "0")
    orig.assert_not_awaited()
