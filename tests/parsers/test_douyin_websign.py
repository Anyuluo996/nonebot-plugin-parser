"""抖音 x-secsdk-web-signature (websign) 签名与 uifid 凭据链测试。

覆盖:
1. encode_pairs 的 URLSearchParams 规则 (仅 ``*-._`` 不转义);
2. sign 的预映像结构 (uifid_timestamp_SALT_query) 与三件套 header,
   预映像在测试内独立手工拼接, 不与实现共享构造路径;
3. uifid 三级来源优先级 (指令持久化 > env > cookie 内 UIFID 字段提取);
4. _request_detail 的 websign 形态注入: 配置 uifid 后请求链四形态,
   a_bogus 全链单次编码 (无 %25), 签名可用发送字节重算复核。

算法出处与线上验证记录见 ``parsers/douyin/websign.py`` 模块文档。

注: nonebot_plugin_parser 系列一律在测试函数内 import (收集期 nonebot
尚未 init, 顶层 import 会炸 localstore, 见 tests/conftest.py)。
"""

import hashlib
from urllib.parse import quote

import pytest

_UIFID = "U" * 320
_STAMP = 1759200000


def test_encode_pairs_urlsearchparams_rules():
    """JS URLSearchParams.toString() 仅 *-._ 不转义, 空格 %20, 非 ASCII UTF-8。"""
    from nonebot_plugin_parser.parsers.douyin import websign

    assert websign.encode_pairs([("a", "x y")]) == "a=x%20y"
    assert websign.encode_pairs([("a", "1*2-3.4_5")]) == "a=1*2-3.4_5"
    assert websign.encode_pairs([("a", "6/7")]) == "a=6%2F7"
    assert websign.encode_pairs([("a", "8+9")]) == "a=8%2B9"
    assert websign.encode_pairs([("d", "图")]) == f"d={quote('图')}"


def test_sign_preimage_and_headers():
    """签名 = md5(uifid_timestamp_SALT_query), query 为编码后的发送字节序。"""
    from nonebot_plugin_parser.parsers.douyin import websign

    params = {"aid": "6383", "aweme_id": "123", "a_bogus": "AbC/dEf+"}
    query, sig, headers = websign.sign(params, _UIFID, timestamp=_STAMP)

    # 预映像独立拼接 (期望串手写), 实现内部 bug 无法自证
    preimage_query = (
        f"aid=6383&aweme_id=123&a_bogus=AbC%2FdEf%2B&uifid={_UIFID}&timestamp={_STAMP}"
    )
    expected = hashlib.md5(
        f"{_UIFID}_{_STAMP}_{websign.SALT}_{preimage_query}".encode()
    ).hexdigest()

    assert sig == expected
    assert query == f"{preimage_query}&x-secsdk-web-signature={expected}"
    assert headers == {
        "uifid": _UIFID,
        "x-secsdk-web-signature": expected,
        "x-secsdk-web-expire": str(_STAMP),
    }


def test_sign_keeps_existing_uifid_position():
    """params 已含 uifid 时保持原位不重复追加 (重复追加即不同预映像被拒)。"""
    from nonebot_plugin_parser.parsers.douyin import websign

    query, _sig, _headers = websign.sign({"uifid": "AAA", "aid": "6383"}, "AAA", timestamp=_STAMP)
    assert query.count("uifid=AAA") == 1
    assert query.startswith("uifid=AAA&")


def test_uifid_from_cookie_extracts_all_spellings():
    from nonebot_plugin_parser.parsers.douyin import ttwid as dy_ttwid

    assert dy_ttwid.uifid_from_cookie(f"ttwid=x; UIFID_TEMP={'A' * 320}; sessionid=y") == "A" * 320
    assert dy_ttwid.uifid_from_cookie(f"a=1; uifid_temp={'B' * 10}") == "B" * 10
    assert dy_ttwid.uifid_from_cookie("a=1; sessionid=x") is None
    assert dy_ttwid.uifid_from_cookie("uifid=") is None


def test_get_effective_uifid_priority(monkeypatch):
    """三级来源: dyuifid 指令持久化 > parser_douyin_uifid env > cookie 内提取。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers.douyin import ttwid as dy_ttwid

    # 1. 指令持久化优先
    monkeypatch.setattr(dy_ttwid, "load_uifid", lambda: "PERSISTED")
    assert dy_ttwid.get_effective_uifid() == "PERSISTED"

    # 2. env 兜底
    monkeypatch.setattr(dy_ttwid, "load_uifid", lambda: None)
    monkeypatch.setattr(pconfig, "parser_douyin_uifid", "FROMENV")
    assert dy_ttwid.get_effective_uifid() == "FROMENV"

    # 3. 完整 cookie 内 UIFID 字段自动提取
    monkeypatch.setattr(pconfig, "parser_douyin_uifid", None)
    monkeypatch.setattr(dy_ttwid, "get_effective_cookie", lambda: f"ttwid=x; UIFID={'C' * 320}")
    assert dy_ttwid.get_effective_uifid() == "C" * 320

    # 全空 → None
    monkeypatch.setattr(dy_ttwid, "get_effective_cookie", lambda: None)
    assert dy_ttwid.get_effective_uifid() is None


def _classify(call: dict) -> str:
    """按 _request_detail 的形态特征分类 (websign 走完整 URL, 无 params)。"""
    headers, params, url = call["headers"], call["params"], str(call["url"])
    if params is not None and "a_bogus" in params:
        return "signed"
    if params is None and "x-secsdk-web-signature=" in url.split("?", 1)[-1]:
        return "websign"
    if headers.get("Origin") == "https://open.douyin.com":
        return "open-api"
    if "Bytespider" in (headers.get("User-Agent") or ""):
        return "bytespider"
    return "unknown"


class _Resp:
    """空 body 响应: 让 _request_detail 走完整条变体链而非首个形态即 break。"""

    status_code = 200
    content = b""

    def raise_for_status(self):
        return None


@pytest.mark.asyncio
async def test_detail_chain_without_uifid_unchanged(monkeypatch):
    """未配置 uifid 时请求链保持三形态, 不注入 websign (回归保护)。"""
    from nonebot_plugin_parser.parsers import DouyinParser

    calls: list[dict] = []

    async def fake_request(_self, url, *, headers=None, params=None, **_kw):
        calls.append({"url": url, "headers": dict(headers or {}), "params": params})
        return _Resp()

    monkeypatch.setattr(DouyinParser, "request", fake_request)
    monkeypatch.setattr(
        "nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_uifid", lambda: None
    )

    parser = DouyinParser()
    await parser._request_detail("7681253720335650091")
    assert [_classify(c) for c in calls] == ["open-api", "signed", "bytespider"]


@pytest.mark.asyncio
async def test_detail_websign_variant_appended_when_uifid_configured(monkeypatch):
    """配置 uifid 后签名形态之后插入 websign 形态: 完整 URL + 三件套 header,
    a_bogus 单次编码, 且签名可用实际发送字节重算复核。"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.parsers.douyin import websign

    calls: list[dict] = []

    async def fake_request(_self, url, *, headers=None, params=None, **_kw):
        calls.append({"url": url, "headers": dict(headers or {}), "params": params})
        return _Resp()

    monkeypatch.setattr(DouyinParser, "request", fake_request)
    monkeypatch.setattr(
        "nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_uifid", lambda: _UIFID
    )

    parser = DouyinParser()
    await parser._request_detail("7681253720335650091")

    forms = [_classify(c) for c in calls]
    assert forms == ["open-api", "signed", "websign", "bytespider"], f"请求链异常: {forms}"

    # 签名形态: a_bogus 应为未编码原文 (旧实现先 quote 再入 dict 会被 httpx
    # 二次编码成 %25, 服务端解码一次得不到签名原文)
    signed_params = calls[1]["params"] or {}
    assert "%" not in signed_params["a_bogus"], "a_bogus 必须是未编码原文, 编码交给 httpx 一次完成"

    # websign 形态: 完整 URL + uifid/签名/expire 三件套 header
    websign_call = calls[2]
    ws_headers = websign_call["headers"]
    assert ws_headers.get("uifid") == _UIFID
    assert ws_headers.get("x-secsdk-web-signature")
    assert ws_headers.get("x-secsdk-web-expire", "").isdigit()

    url = str(websign_call["url"])
    assert url.startswith("https://www.douyin.com/aweme/v1/web/aweme/detail/?")
    query = url.split("?", 1)[1]
    # 单次编码不变量: 全部值为原文编码一次, 不存在二次编码痕迹
    assert "%25" not in query, "query 内出现 %25 说明发生了双重编码"
    # 签名参数必须在末尾
    assert not query.startswith("x-secsdk-web-signature=")
    assert "&x-secsdk-web-signature=" in query
    hashed_query, sig_param = query.rsplit("&x-secsdk-web-signature=", 1)
    # 签名可用实际发送字节 + header 内时间戳重算复核 (端到端一致性)
    preimage = f"{_UIFID}_{ws_headers['x-secsdk-web-expire']}_{websign.SALT}_{hashed_query}"
    assert (
        hashlib.md5(preimage.encode()).hexdigest() == sig_param == ws_headers["x-secsdk-web-signature"]
    )
    # uifid 进 query 恰一次, timestamp 在其后
    assert hashed_query.count(f"uifid={_UIFID}") == 1
    assert f"uifid={_UIFID}&timestamp=" in hashed_query
