"""抖音直播链接解析测试（封面卡片形态，不取直播流）。

线上案例 (2026-11-06)：群友发直播分享短链 v.douyin.com/OVtu3v_LZTM，
重定向落点 webcast.amemv.com/douyin/webcast/reflow/<room_id> 旧版无对应
模式，跨 parser 路由也无人接 → ParseException 无法匹配，即时重试 4 次 +
L2 三轮全灭。接口 reflow/info 匿名直连可用（同日 yun 容器网络栈实测）。
"""

import pytest

# 线上真实落点（该房间 2026-11-06 16:35 已下播，reflow/info 仍返回房间信息+封面）
REFLOW_URL = (
    "https://webcast.amemv.com/douyin/webcast/reflow/7704962143841422080"
    "?u_code=1l3d36mgdh49&did=MS4wLjABAAAAKYeMxDo9XJF97hByRWaldyAq8kqPJei7GI8pQYVJaaQN_lCOtnOH1v7V6TqWuJE0"
)

# 网页端数字房间号形态
LIVE_WEB_URL = "https://live.douyin.com/745964462470"

_ROOM_LIVING = {
    "status_code": 0,
    "data": {
        "room": {
            "id_str": "7704962143841422080",
            "title": "一只鱼笨笨正在直播",
            "status": 2,
            "user_count": 12,
            "finish_time": 0,
            "share_url": REFLOW_URL,
            "cover": {"url_list": ["https://p3-webcast.douyinpic.com/img/cover.jpg"]},
            "owner": {
                "nickname": "一只鱼笨笨",
                "avatar_thumb": {"url_list": ["https://p3.douyinpic.com/aweme/avatar.jpg"]},
            },
        }
    },
}

# 下播房间：finish_time 已填。stream_url 也会返回分辨率表，不可用于判活，
# 本用例故意不返回 stream_url 以外的差异字段，覆盖真实下播响应形态
_ROOM_ENDED = {
    "status_code": 0,
    "data": {
        "room": {
            "id_str": "7704962143841422080",
            "title": "一只鱼笨笨正在直播",
            "status": 2,
            "user_count": 2,
            "finish_time": 1793955298,
            "cover": {"url_list": ["https://p3-webcast.douyinpic.com/img/cover.jpg"]},
            "owner": {"nickname": "一只鱼笨笨", "avatar_thumb": {"url_list": []}},
        }
    },
}


def test_search_url_matches_live_links():
    """webcast reflow 落点与 live.douyin.com 数字房间号都应被抖音 parser 匹配。"""
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()

    keyword, searched = parser.search_url(REFLOW_URL)
    assert keyword == "webcast.amemv"
    assert searched.group("rid") == "7704962143841422080"

    keyword, searched = parser.search_url(LIVE_WEB_URL)
    assert keyword == "live.douyin"
    assert searched.group("rid") == "745964462470"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("room_payload", "expect_living"),
    [pytest.param(_ROOM_LIVING, True, id="living"), pytest.param(_ROOM_ENDED, False, id="ended")],
)
async def test_parse_live_cover_only(monkeypatch, room_payload, expect_living):
    """reflow/info 房间数据 → 封面图片 + 标题/主播/状态文案，不产生视频内容。"""
    import asyncio
    from pathlib import Path

    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.parsers import base as parser_base

    def _fake_download_img(_url, **_kwargs):
        # download_img 被 @auto_task 包装为调用即返回 Task，这里保持同形态
        return asyncio.ensure_future(asyncio.sleep(0, result=Path("/tmp") / "fake_img.jpg"))

    monkeypatch.setattr(parser_base.DOWNLOADER, "download_img", _fake_download_img)

    class _FakeResp:
        def json(self):
            return room_payload

    async def _fake_request(_url, **_kwargs):
        # 挂实例属性不绑定 self: self.request(url, ...) 首参即 url
        return _FakeResp()

    parser = DouyinParser()
    monkeypatch.setattr(parser, "request", _fake_request)

    keyword, searched = parser.search_url(REFLOW_URL)
    result = await parser.parse(keyword, searched)

    assert result.title == "一只鱼笨笨正在直播"
    assert result.author is not None, "作者信息缺失"
    assert result.author.name == "一只鱼笨笨"
    assert result.img_contents, "封面图片缺失"
    assert not result.video_contents, "直播解析不应产生视频内容"
    text = result.text or ""
    assert ("直播中" in text) == expect_living, f"状态文案不符: {text}"
    if expect_living:
        assert "12" in text, f"直播中应带观看数: {text}"
    if not expect_living:
        assert result.url, "下播房间应保留房间链接"


@pytest.mark.asyncio
async def test_parse_live_room_not_found_tip(monkeypatch):
    """服务端业务码非 0（房间不存在）应抛 TipException 而非可重试 ParseException。"""
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import TipException

    class _FakeResp:
        def json(self):
            return {"status_code": 1700, "data": None, "extra": {}}

    async def _fake_request(_url, **_kwargs):
        return _FakeResp()

    parser = DouyinParser()
    monkeypatch.setattr(parser, "request", _fake_request)

    keyword, searched = parser.search_url(REFLOW_URL)
    with pytest.raises(TipException, match="直播间不存在"):
        await parser.parse(keyword, searched)
    # TipException 是 ParseException 子类, parse_retry 对其显式跳过即时重试,
    # 不会把确定性失败塞进 L2 队列空转 (与抖音删除作品的处理一致)


@pytest.mark.asyncio
async def test_parse_live_real_reflow():
    """线上回归：真实 reflow/info 直连，下播房间仍应返回封面卡片。"""
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    keyword, searched = parser.search_url(REFLOW_URL)
    try:
        result = await parser.parse(keyword, searched)
    except Exception as e:
        pytest.skip(f"直播间接口不可用或被风控，跳过: {e}")

    assert result.img_contents, "封面缺失"
    assert result.title, "标题为空"
