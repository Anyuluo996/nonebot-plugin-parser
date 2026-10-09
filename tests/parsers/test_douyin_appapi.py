"""抖音安卓 App detail 端点 (aweme.snssdk.com) 免签名形态测试。

覆盖:
1. 设备参数解析 (parser_douyin_app_device_profile): 白名单过滤 / 类型归一 /
   必填校验 / 非法输入整体停用;
2. build_request 请求形态: X-SS-No-Cookie 游客态头、无 Referer/Cookie、
   share 场景参数与每次刷新的 ts(秒)/_rticket(毫秒);
3. _request_detail 变体链: 配置设备参数后 open-api 失败切换 app-api
   (不同 host/风控域), 响应可被 slides.decode_aweme_detail 直接解码;
   未配置时链路保持原状不注入。

设备参数是可被风控的个体标识, 本文件全部用假值, 不提交真实提取结果。
注: 测试函数内 import (收集期 nonebot 尚未 init, 见 tests/conftest.py)。
"""

import json

import pytest

# 形如真机提取结构的假设备参数 (数值型字段 app 上报为数字, 白名单外字段混入一条)
_PROFILE_JSON = json.dumps(
    {
        "iid": "123456789012345",
        "device_id": "987654321098765",
        "aid": "1128",
        "version_code": "400600",
        "version_name": "40.6.0",
        "cdid": "00000000-0000-0000-0000-000000000000",
        "is_android_pad": 1,
        "totally_unknown_field": "should-be-dropped",
    }
)

_DETAIL_JSON = json.dumps(
    {
        "aweme_detail": {
            "author": {
                "nickname": "appapi-tester",
                "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
            },
            "desc": "app-api endpoint regression",
            "create_time": 1757200000,
            "images": None,
            "video": {
                "play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=vtest"]},
                "cover": {"url_list": ["https://example.com/cover.jpg"]},
                "duration": 15000,
            },
        }
    }
).encode()


def _classify(call: dict) -> str:
    """按 _request_detail 的形态特征分类 (app-api 走 snssdk host)。"""
    headers, params, url = call["headers"], call["params"], str(call["url"])
    if "aweme.snssdk.com" in url:
        return "app-api"
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
    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        return None


def test_parse_profile_valid_normalizes_values():
    """合法 profile: 数值转字符串透传, 白名单外字段丢弃, 必填齐全时返回。"""
    from nonebot_plugin_parser.parsers.douyin import appapi

    profile = appapi.parse_profile(_PROFILE_JSON)
    assert profile == {
        "iid": "123456789012345",
        "device_id": "987654321098765",
        "aid": "1128",
        "version_code": "400600",
        "version_name": "40.6.0",
        "cdid": "00000000-0000-0000-0000-000000000000",
        "is_android_pad": "1",
    }


def test_parse_profile_accepts_dict_from_nonebot_env():
    """dict 输入直收: NoneBot .env 管线会把形似 JSON 的值解码成 dict 再进
    pydantic (部署实测 1c3ec6e 曾因 str-only 声明炸插件导入), 两种形态等价。"""
    from nonebot_plugin_parser.parsers.douyin import appapi

    raw_dict = json.loads(_PROFILE_JSON)
    assert appapi.parse_profile(raw_dict) == appapi.parse_profile(_PROFILE_JSON)
    # dict 形态下缺必填同样整体停用
    assert appapi.parse_profile({"aid": "1128"}) is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "not-json{",
        "[1, 2]",
        json.dumps({"device_id": "987654321098765", "aid": "1128", "version_code": "400600"}),
        json.dumps({"iid": "", "device_id": "987654321098765", "aid": "1128", "version_code": "400600"}),
    ],
)
def test_parse_profile_invalid_returns_none(raw):
    """空/非 JSON/非对象/缺必填字段 (含空串) → None, 该形态整体停用。"""
    from nonebot_plugin_parser.parsers.douyin import appapi

    assert appapi.parse_profile(raw) is None


def test_parse_profile_rejects_bool_and_nested_values():
    """bool (JSON true) 与嵌套对象字段丢弃, 但不影响其余合法字段。"""
    from nonebot_plugin_parser.parsers.douyin import appapi

    raw = json.dumps(
        {
            "iid": "123456789012345",
            "device_id": "987654321098765",
            "aid": "1128",
            "version_code": "400600",
            "cpu_support64": True,
            "device_type": {"model": "hack"},
        }
    )
    profile = appapi.parse_profile(raw)
    assert profile is not None
    assert "cpu_support64" not in profile
    assert "device_type" not in profile
    assert profile["iid"] == "123456789012345"


def test_build_request_guest_form_and_fresh_timestamps():
    """游客态请求形态: 无 Cookie/Referer, ts(秒)/_rticket(毫秒) 每次刷新。"""
    from nonebot_plugin_parser.parsers.douyin import appapi

    profile = appapi.parse_profile(_PROFILE_JSON)
    headers, params = appapi.build_request(profile, "7681253720335650091")

    assert headers["X-SS-No-Cookie"] == "1"
    assert headers["x-ss-dp"] == "1128"
    assert headers["x-tt-store-region"] == "cn"
    assert "Cookie" not in headers, "app-api 形态不携带 Cookie"
    assert "Referer" not in headers
    assert "Origin" not in headers

    assert params["aweme_id"] == "7681253720335650091"
    assert params["aid"] == "1128"
    assert params["iid"] == "123456789012345"
    assert params["origin_type"] == "share"
    assert params["ts"].isdigit(), "ts 必须是数字"
    assert len(params["ts"]) == 10, "ts 必须是秒级时间戳"
    assert params["_rticket"].isdigit(), "_rticket 必须是数字"
    assert len(params["_rticket"]) == 13, "_rticket 必须是毫秒"


@pytest.mark.asyncio
async def test_appapi_variant_used_when_openapi_empty(monkeypatch):
    """open-api 被风控 (200 空 body) 时切换 app-api, 响应可直接用现有结构解码。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.parsers.douyin import slides

    monkeypatch.setattr(pconfig, "parser_douyin_app_device_profile", _PROFILE_JSON)
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_credential", lambda: None)
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_uifid", lambda: None)

    calls: list[dict] = []

    async def fake_request(_self, url, *, headers=None, params=None, **_kw):
        call = {
            "url": str(url),
            "headers": dict(headers or {}),
            "params": dict(params) if params is not None else None,
        }
        calls.append(call)
        if _classify(call) == "open-api":
            return _Resp(b"")  # open-api 被风控: 200 + 空 body
        if _classify(call) == "app-api":
            return _Resp(_DETAIL_JSON)
        return _Resp(b"")

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    response = await parser._request_detail("7681253720335650091")
    assert response is not None, "五种形态全部失败"
    assert response.content, "app-api 形态未取回数据"

    forms = [_classify(c) for c in calls]
    assert forms == ["open-api", "app-api"], f"open-api 失败后应立即切换 app-api: {forms}"

    app_call = calls[1]
    assert app_call["url"].startswith("https://aweme.snssdk.com/aweme/v1/aweme/detail/")
    assert app_call["headers"]["X-SS-No-Cookie"] == "1"
    assert app_call["params"]["aweme_id"] == "7681253720335650091"

    # App 端点响应结构与 web 端一致: 现有 slides 双格式解码器无需改动
    aweme = slides.decode_aweme_detail(response.content)
    assert aweme is not None, "App 端点响应解码失败"
    assert aweme.desc == "app-api endpoint regression"
    assert aweme.name == "appapi-tester"


@pytest.mark.asyncio
async def test_appapi_variant_skipped_without_profile(monkeypatch):
    """未配置设备参数时链路保持原三形态, 不注入 app-api。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import DouyinParser

    monkeypatch.setattr(pconfig, "parser_douyin_app_device_profile", None)
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_credential", lambda: None)
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_uifid", lambda: None)

    calls: list[dict] = []

    async def fake_request(_self, url, *, headers=None, params=None, **_kw):
        calls.append(
            {"url": str(url), "headers": dict(headers or {}), "params": params if params is None else dict(params)}
        )
        return _Resp(b"")

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    await parser._request_detail("7681253720335650091")
    assert [_classify(c) for c in calls] == ["open-api", "signed", "bytespider"]


# === story 渠道过滤后的 App 直连重试 ===
# 实战案例 (2026-10-10, 7694643968583141755「租房李老师」): open-api 返回
# 非空 story_25_filter 过滤体, 变体链"非空即成功"即停, parse_slides 判定
# 「故事」放弃; App 端点对该作品实际下发全量数据 → story 分支定向补试。

_STORY_FILTER_JSON = json.dumps(
    {
        "status_code": 0,
        "filter_detail": {"aweme_id": "7694643968583141755", "filter_reason": "story_25_filter"},
    }
).encode()


def _pin_credentials(monkeypatch):
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_credential", lambda: None)
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_uifid", lambda: None)


@pytest.mark.asyncio
async def test_story_filter_retried_via_app_endpoint(monkeypatch):
    """open-api 返回 story 过滤体 → App 直连拿到全量数据, 正常产出解析结果。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import DouyinParser

    monkeypatch.setattr(pconfig, "parser_douyin_app_device_profile", _PROFILE_JSON)
    _pin_credentials(monkeypatch)

    async def fake_request(_self, url, *, headers=None, params=None, **_kw):
        call = {"url": str(url), "headers": dict(headers or {}), "params": params if params is None else dict(params)}
        if _classify(call) == "app-api":
            return _Resp(_DETAIL_JSON)
        return _Resp(_STORY_FILTER_JSON)  # open-api (非空过滤体, 链内即停)

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    result = await parser.parse_slides("7694643968583141755")
    assert result.title == "app-api endpoint regression", "story 过滤后应由 App 直连兜住"
    assert result.author is not None, "作者为空"
    assert result.author.name == "appapi-tester"


@pytest.mark.asyncio
async def test_story_filter_without_app_profile_tips(monkeypatch):
    """未配置设备参数时 story 过滤维持原行为: 直接 TipException。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import TipException

    monkeypatch.setattr(pconfig, "parser_douyin_app_device_profile", None)
    _pin_credentials(monkeypatch)

    async def fake_request(_self, _url, **_kw):
        return _Resp(_STORY_FILTER_JSON)

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    with pytest.raises(TipException, match="故事"):
        await parser.parse_slides("7694643968583141755")


@pytest.mark.asyncio
async def test_story_filter_app_endpoint_empty_falls_to_tip(monkeypatch):
    """App 直连也拿不到数据 (空 body) 时回退 TipException, 不误报成功。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import TipException

    monkeypatch.setattr(pconfig, "parser_douyin_app_device_profile", _PROFILE_JSON)
    _pin_credentials(monkeypatch)

    async def fake_request(_self, _url, **_kw):
        return _Resp(_STORY_FILTER_JSON)

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    with pytest.raises(TipException, match="故事"):
        await parser.parse_slides("7694643968583141755")
