"""音乐点歌 VIP 过滤与网易云登录态单元测试。

覆盖：
- 三平台付费判定（netease fee / qqmusic pay_play / kugou pay_type）
- 登录态联动：有登录态不过滤 VIP，无登录态过滤
- 搜索补齐：过滤后自动多搜，截断到 limit
- 网易云 credential 持久化往返
"""

from unittest.mock import AsyncMock

import httpx
import pytest


# --------------------------------------------------------------------------- #
# 付费判定函数
# --------------------------------------------------------------------------- #
class TestPaidDetection:
    def test_netease_free(self):
        from nonebot_plugin_parser.music_search import _is_paid_song_netease

        assert _is_paid_song_netease({"fee": 0}) is False

    def test_netease_vip(self):
        from nonebot_plugin_parser.music_search import _is_paid_song_netease

        assert _is_paid_song_netease({"fee": 1}) is True

    def test_netease_low_quality_free_preserved(self):
        # fee=8 是低音质免费（可播），应保留（不过滤）
        from nonebot_plugin_parser.music_search import _is_paid_song_netease

        assert _is_paid_song_netease({"fee": 8}) is False

    def test_netease_missing_fee_defaults_free(self):
        from nonebot_plugin_parser.music_search import _is_paid_song_netease

        assert _is_paid_song_netease({}) is False

    def test_kugou_free(self):
        from nonebot_plugin_parser.music_search import _is_paid_song_kugou

        assert _is_paid_song_kugou({"pay_type": 0}) is False

    def test_kugou_paid(self):
        from nonebot_plugin_parser.music_search import _is_paid_song_kugou

        assert _is_paid_song_kugou({"pay_type": 3}) is True

    def test_kugou_missing_pay_type_defaults_free(self):
        from nonebot_plugin_parser.music_search import _is_paid_song_kugou

        assert _is_paid_song_kugou({}) is False


# --------------------------------------------------------------------------- #
# 登录态联动（_is_paid_filter_enabled）
# --------------------------------------------------------------------------- #
class TestFilterEnabled:
    def test_kugou_always_filter(self):
        """酷狗暂无登录功能，恒过滤。"""
        from nonebot_plugin_parser.music_search import _is_paid_filter_enabled

        assert _is_paid_filter_enabled("kugou") is True

    def test_netease_filter_when_no_credential(self, monkeypatch):
        from nonebot_plugin_parser.music_search import _is_paid_filter_enabled
        from nonebot_plugin_parser.parsers.netease import credential as netease_cred

        monkeypatch.setattr(netease_cred, "is_available", lambda: False)
        assert _is_paid_filter_enabled("netease") is True

    def test_netease_no_filter_when_logged_in(self, monkeypatch):
        from nonebot_plugin_parser.music_search import _is_paid_filter_enabled
        from nonebot_plugin_parser.parsers.netease import credential as netease_cred

        monkeypatch.setattr(netease_cred, "is_available", lambda: True)
        assert _is_paid_filter_enabled("netease") is False

    def test_qqmusic_filter_when_no_credential(self, monkeypatch):
        from nonebot_plugin_parser.music_search import _is_paid_filter_enabled
        from nonebot_plugin_parser.parsers.qqmusic import credential as qq_cred

        monkeypatch.setattr(qq_cred, "is_available", lambda: False)
        assert _is_paid_filter_enabled("qqmusic") is True

    def test_qqmusic_no_filter_when_logged_in(self, monkeypatch):
        from nonebot_plugin_parser.music_search import _is_paid_filter_enabled
        from nonebot_plugin_parser.parsers.qqmusic import credential as qq_cred

        monkeypatch.setattr(qq_cred, "is_available", lambda: True)
        assert _is_paid_filter_enabled("qqmusic") is False


# --------------------------------------------------------------------------- #
# 搜索过滤 + 补齐
# --------------------------------------------------------------------------- #
def _netease_song(song_id: int, name: str, fee: int) -> dict:
    return {"id": song_id, "name": name, "fee": fee, "duration": 180000, "artists": [{"name": "测试歌手"}]}


@pytest.mark.asyncio
async def test_search_netease_filters_vip_when_not_logged_in(monkeypatch):
    """未登录时过滤 VIP（fee=1），保留免费和低音质免费（fee=8）。"""
    from nonebot_plugin_parser.parsers.base import BaseParser
    from nonebot_plugin_parser.parsers.netease import credential as netease_cred

    monkeypatch.setattr(netease_cred, "is_available", lambda: False)
    monkeypatch.setattr(
        BaseParser,
        "request",
        AsyncMock(return_value=_mock_response(200, {"result": {"songs": [
            _netease_song(1, "免费1", 0),
            _netease_song(2, "VIP1", 1),
            _netease_song(3, "免费2", 0),
            _netease_song(4, "VIP2", 1),
            _netease_song(5, "低音质免费", 8),
        ]}})),
    )
    from nonebot_plugin_parser.music_search import search_netease

    parser = BaseParser()
    items = await search_netease(parser, "测试", limit=5)

    names = [it.name for it in items]
    assert "VIP1" not in names
    assert "VIP2" not in names
    assert len(items) == 3  # 2 免费 + 1 低音质免费


@pytest.mark.asyncio
async def test_search_netease_keeps_vip_when_logged_in(monkeypatch):
    """已登录时不过滤 VIP。"""
    from nonebot_plugin_parser.music_search import search_netease
    from nonebot_plugin_parser.parsers.base import BaseParser
    from nonebot_plugin_parser.parsers.netease import credential as netease_cred

    monkeypatch.setattr(netease_cred, "is_available", lambda: True)
    monkeypatch.setattr(
        BaseParser,
        "request",
        AsyncMock(return_value=_mock_response(200, {"result": {"songs": [
            _netease_song(1, "免费", 0),
            _netease_song(2, "VIP", 1),
        ]}})),
    )
    parser = BaseParser()
    items = await search_netease(parser, "测试", limit=5)
    assert len(items) == 2  # VIP 保留


@pytest.mark.asyncio
async def test_search_kugou_filters_paid(monkeypatch):
    """酷狗过滤付费（pay_type=3）。"""
    from nonebot_plugin_parser.music_search import search_kugou
    from nonebot_plugin_parser.parsers.base import BaseParser

    monkeypatch.setattr(
        BaseParser,
        "request",
        AsyncMock(return_value=_mock_response(200, {"errcode": 0, "data": {"info": [
            {"hash": "h1", "songname": "免费", "singername": "歌手A", "duration": 200, "pay_type": 0},
            {"hash": "h2", "songname": "付费", "singername": "歌手B", "duration": 200, "pay_type": 3},
            {"hash": "h3", "songname": "免费2", "singername": "歌手C", "duration": 200, "pay_type": 0},
        ]}})),
    )
    parser = BaseParser()
    items = await search_kugou(parser, "测试", limit=5)
    names = [it.name for it in items]
    assert "付费" not in names
    assert len(items) == 2


@pytest.mark.asyncio
async def test_search_netease_truncates_to_limit(monkeypatch):
    """过滤后结果截断到 limit。"""
    from nonebot_plugin_parser.music_search import search_netease
    from nonebot_plugin_parser.parsers.base import BaseParser
    from nonebot_plugin_parser.parsers.netease import credential as netease_cred

    monkeypatch.setattr(netease_cred, "is_available", lambda: False)
    songs = [_netease_song(i, f"歌{i}", 0) for i in range(7)]
    monkeypatch.setattr(
        BaseParser,
        "request",
        AsyncMock(return_value=_mock_response(200, {"result": {"songs": songs}})),
    )
    parser = BaseParser()
    items = await search_netease(parser, "测试", limit=3)
    assert len(items) == 3


# --------------------------------------------------------------------------- #
# 网易云 credential 持久化往返
# --------------------------------------------------------------------------- #
class TestNeteaseCredential:
    def test_save_load_clear_roundtrip(self, monkeypatch, tmp_path):
        from nonebot_plugin_parser.parsers.netease import credential as netease_cred

        cred_file = tmp_path / "netease_credential.json"
        monkeypatch.setattr(netease_cred, "_CRED_FILE", cred_file)

        assert netease_cred.load_credential() is None
        assert netease_cred.is_available() is False

        cookie = "MUSIC_U=abc123; __csrf=xyz"
        netease_cred.save_credential(cookie)
        assert netease_cred.is_available() is True
        assert netease_cred.load_credential() == cookie

        assert netease_cred.clear_credential() is True
        assert netease_cred.load_credential() is None
        assert netease_cred.clear_credential() is False

    def test_load_rejects_cookie_without_music_u(self, monkeypatch, tmp_path):
        from nonebot_plugin_parser.parsers.netease import credential as netease_cred

        cred_file = tmp_path / "netease_credential.json"
        monkeypatch.setattr(netease_cred, "_CRED_FILE", cred_file)
        netease_cred.save_credential("__csrf=xyz; other=val")
        assert netease_cred.load_credential() is None


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _mock_response(status: int, json_data: dict) -> httpx.Response:
    """构造一个带 JSON 的假 httpx.Response。"""
    import json

    return httpx.Response(status, content=json.dumps(json_data).encode(), headers={"content-type": "application/json"})


def test_search_item_display():
    """SearchItem 展示文本格式。"""
    from nonebot_plugin_parser.music_search import SearchItem

    item = SearchItem(platform="netease", song_id="1", name="晴天", artist="周杰伦")
    assert item.display == "晴天 - 周杰伦"
    assert item.platform_display == "网易云"

