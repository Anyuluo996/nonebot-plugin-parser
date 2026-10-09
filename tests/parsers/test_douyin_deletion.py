"""抖音作品删除判定测试。

覆盖:
1. slides.is_deleted_response 的判定规则: deleted 形态 filter_reason /
   中英文「已删除」提示语命中, 「未删除」否定与渠道过滤 (story/copyright)
   不误判, 非 JSON/空响应不认定删除;
2. parse_slides 的两道拦截: aweme_detail 缺失 + 删除证据 → TipException;
   aweme_detail 完整下发但 status.is_delete 置位 → TipException;
   无删除标记的正常 detail 不受影响。

判定规则对齐 Node 参照实现 jiexi 的错误分类 (2026-10-10)。
注: 测试函数内 import (收集期 nonebot 尚未 init, 见 tests/conftest.py)。
"""

import json

import pytest


def _raw(data) -> bytes:
    return json.dumps(data).encode()


_DELETED_ID = "7681253720335650091"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # filter_reason 的 deleted 形态 (裸 deleted / 带前缀 / 大小写不敏感)
        ({"filter_detail": {"filter_reason": "deleted"}}, True),
        ({"filter_detail": {"filter_reason": "status_deleted"}}, True),
        ({"filter_detail": {"filter_reason": "AWEME_DELETED"}}, True),
        # status_msg / filter 提示语命中
        ({"status_msg": "很抱歉，该作品已删除"}, True),
        ({"status_msg": "Sorry, this video has been deleted."}, True),
        ({"filter_detail": {"notice": "该内容已被删除"}}, True),
        ({"filter_detail": {"detail_msg": "内容被删除了"}}, True),
        # 带施动者的插入形态
        ({"status_msg": "该视频已被作者删除"}, True),
        ({"status_msg": "图文已被发布者删除"}, True),
        # 否定表述不误判
        ({"status_msg": "作品未删除，仍在审核中"}, False),
        ({"status_msg": "该内容并没有被删除"}, False),
        ({"status_msg": "this post has not been deleted"}, False),
        # 施动者形态的否定 (与正向分支对应的否定覆盖)
        ({"status_msg": "该视频未被作者删除"}, False),
        ({"status_msg": "作品没有被系统删除"}, False),
        # 渠道过滤 / 业务错误不是删除
        ({"filter_detail": {"filter_reason": "story_25_filter"}}, False),
        ({"filter_detail": {"filter_reason": "copyright_foreign"}}, False),
        ({"status_code": 8, "status_msg": "内容不存在"}, False),
        ({}, False),
    ],
)
def test_is_deleted_response_rules(payload, expected):
    from nonebot_plugin_parser.parsers.douyin import slides

    assert slides.is_deleted_response(_raw(payload)) is expected


@pytest.mark.parametrize("raw", [b"", b"not json", b"null", b"[1, 2]"])
def test_is_deleted_response_non_object_false(raw):
    from nonebot_plugin_parser.parsers.douyin import slides

    assert slides.is_deleted_response(raw) is False


def _deleted_response() -> bytes:
    """aweme_detail 不下发 + 明确删除证据的典型响应。"""
    return _raw(
        {
            "status_code": 0,
            "filter_detail": {"aweme_id": _DELETED_ID, "filter_reason": "status_deleted"},
        }
    )


def _detail_payload(extra: dict) -> bytes:
    """构造完整 aweme_detail 响应, extra 合并进顶层作品对象。"""
    detail = {
        "author": {
            "nickname": "deletion-tester",
            "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
        },
        "desc": "detail body regression",
        "create_time": 1757200000,
        "images": None,
        "video": {
            "play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=vtest"]},
            "cover": {"url_list": ["https://example.com/cover.jpg"]},
            "duration": 15000,
        },
    }
    detail.update(extra)
    return _raw({"aweme_detail": detail})


def _pin_no_extras(monkeypatch):
    """钉住凭据/设备参数全空, 请求链收敛为 open-api 首个形态即返回。"""
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_credential", lambda: None)
    monkeypatch.setattr("nonebot_plugin_parser.parsers.douyin.ttwid.get_effective_uifid", lambda: None)
    monkeypatch.setattr("nonebot_plugin_parser.config.pconfig.parser_douyin_app_device_profile", None)


class _Resp:
    def __init__(self, content: bytes):
        self.status_code = 200
        self.content = content

    def raise_for_status(self):
        return None


@pytest.mark.asyncio
async def test_parse_slides_deleted_filter_tip(monkeypatch):
    """删除证据 + 无 aweme_detail → TipException 明确提示, 不报笼统解析失败。"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import TipException

    _pin_no_extras(monkeypatch)

    async def fake_request(_self, _url, **_kw):
        return _Resp(_deleted_response())

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    with pytest.raises(TipException, match="已被删除"):
        await parser.parse_slides(_DELETED_ID)


@pytest.mark.asyncio
async def test_parse_slides_status_msg_deleted_tip(monkeypatch):
    """无 filter_detail、仅 status_msg 含删除提示时同样命中。"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import TipException

    _pin_no_extras(monkeypatch)

    async def fake_request(_self, _url, **_kw):
        return _Resp(_raw({"status_code": 0, "status_msg": "该作品已删除"}))

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    with pytest.raises(TipException, match="已被删除"):
        await parser.parse_slides(_DELETED_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [1, True], ids=["int", "bool"])
async def test_parse_slides_parsed_detail_is_delete_tip(monkeypatch, flag):
    """detail 完整下发但 status.is_delete 置位 (int/bool 双形态) → 构建内容前拦截。"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import TipException

    _pin_no_extras(monkeypatch)

    async def fake_request(_self, _url, **_kw):
        return _Resp(_detail_payload({"status": {"is_delete": flag}}))

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    with pytest.raises(TipException, match="已被删除"):
        await parser.parse_slides(_DELETED_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"status": {"is_delete": 0}}, {"status": {"is_delete": False}}])
async def test_parse_slides_no_delete_flag_parses_normally(monkeypatch, extra):
    """status 缺失 / is_delete=0/false 均不触发删除拦截, 正常产出结果。"""
    from nonebot_plugin_parser.parsers import DouyinParser

    _pin_no_extras(monkeypatch)

    async def fake_request(_self, _url, **_kw):
        return _Resp(_detail_payload(extra))

    monkeypatch.setattr(DouyinParser, "request", fake_request)

    parser = DouyinParser()
    result = await parser.parse_slides(_DELETED_ID)
    assert result.title == "detail body regression", f"extra={extra!r} 不应触发删除拦截"
