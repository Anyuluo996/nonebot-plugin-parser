"""点歌搜索聚合层单元测试。

使用 mock 隔离真实网络,验证:
- 三服务字段映射正确(网易云/QQ/酷狗各一个)
- 单服务失败静默降级(不抛异常,其他服务补齐)
- 合并去重
- global_limit 截断
- 静默失败原则(空结果不报错)

注意: nonebot_plugin_parser 在模块顶层 require localstore,必须在测试函数内导入
(此时 conftest 的 init_nonebot fixture 已完成 NoneBot 初始化)。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_response(json_data: dict | None = None, status: int = 200, text: str = "") -> MagicMock:
    """构造一个假的 httpx.Response。"""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = text
    return resp


@pytest.mark.asyncio
async def test_search_netease_field_mapping():
    """网易云搜索字段映射: id/name/artists[].name/duration(ms→s)。"""
    from nonebot_plugin_parser.music_search import search_netease

    parser = MagicMock()
    parser.request = AsyncMock(
        return_value=_mock_response(
            {
                "result": {
                    "songs": [
                        {
                            "id": 12345,
                            "name": "晴天",
                            "duration": 269000,  # ms
                            "artists": [{"name": "周杰伦"}, {"name": "方文山"}],
                            "album": {"artist": {"img1v1Url": "http://x/cover.jpg"}},
                        }
                    ]
                }
            }
        )
    )
    items = await search_netease(parser, "晴天", limit=5)
    assert len(items) == 1
    it = items[0]
    assert it.platform == "netease"
    assert it.song_id == "12345"
    assert it.name == "晴天"
    assert it.artist == "周杰伦 / 方文山"
    assert it.duration == pytest.approx(269.0)  # ms → s
    assert it.pic_url == "http://x/cover.jpg"


@pytest.mark.asyncio
async def test_search_netease_failure_silent():
    """网易云搜索失败(异常)静默返回空列表。"""
    from nonebot_plugin_parser.music_search import search_netease

    parser = MagicMock()
    parser.request = AsyncMock(side_effect=RuntimeError("network down"))
    items = await search_netease(parser, "kw", limit=5)
    assert items == []


@pytest.mark.asyncio
async def test_search_kugou_field_mapping():
    """酷狗搜索字段映射: hash/songname/singername/duration(秒)。"""
    from nonebot_plugin_parser.music_search import search_kugou

    parser = MagicMock()
    parser.request = AsyncMock(
        return_value=_mock_response(
            {
                "errcode": 0,
                "data": {
                    "info": [
                        {
                            "hash": "abc123hash",
                            "songname": "晴天",
                            "singername": "周杰伦",
                            "duration": 269,
                        }
                    ]
                },
            }
        )
    )
    items = await search_kugou(parser, "晴天", limit=5)
    assert len(items) == 1
    it = items[0]
    assert it.platform == "kugou"
    assert it.song_id == "abc123hash"
    assert it.name == "晴天"
    assert it.artist == "周杰伦"
    assert it.duration == pytest.approx(269.0)


@pytest.mark.asyncio
async def test_search_kugou_errcode_nonzero_silent():
    """酷狗返回非 0 errcode 静默返回空。"""
    from nonebot_plugin_parser.music_search import search_kugou

    parser = MagicMock()
    parser.request = AsyncMock(return_value=_mock_response({"errcode": 100, "data": {}}))
    assert await search_kugou(parser, "kw") == []


@pytest.mark.asyncio
async def test_search_qqmusic_unavailable_silent():
    """qqmusic-api-python 未安装时静默返回空。"""
    from nonebot_plugin_parser.music_search import search_qqmusic

    parser = MagicMock()
    # 让 import qqmusic_api 抛 ImportError
    import sys

    original = sys.modules.get("qqmusic_api")
    sys.modules["qqmusic_api"] = None  # type: ignore[assignment]
    try:
        items = await search_qqmusic(parser, "kw")
        assert items == []
    finally:
        if original is not None:
            sys.modules["qqmusic_api"] = original
        else:
            sys.modules.pop("qqmusic_api", None)


@pytest.mark.asyncio
async def test_aggregate_search_single_failure_fallback():
    """单服务失败(异常)被静默,其他服务补齐。"""
    from nonebot_plugin_parser.music_search import SearchItem, aggregate_search

    parser = MagicMock()
    netease_items = [SearchItem("netease", "1", "歌A", "周杰伦")]  # type: ignore[arg-type]
    kugou_items = [SearchItem("kugou", "h1", "歌B", "周杰伦")]  # type: ignore[arg-type]
    with patch(
        "nonebot_plugin_parser.music_search.search_netease",
        new=AsyncMock(return_value=netease_items),
    ), patch(
        "nonebot_plugin_parser.music_search.search_qqmusic",
        new=AsyncMock(side_effect=RuntimeError("qq down")),
    ), patch(
        "nonebot_plugin_parser.music_search.search_kugou",
        new=AsyncMock(return_value=kugou_items),
    ):
        merged = await aggregate_search(parser, "kw", ["netease", "qqmusic", "kugou"])
    # QQ 失败静默, 网易云 + 酷狗 2 条都在
    assert len(merged) == 2
    platforms = [it.platform for it in merged]
    assert "netease" in platforms
    assert "kugou" in platforms
    assert "qqmusic" not in platforms  # QQ 失败, 不应出现


@pytest.mark.asyncio
async def test_aggregate_search_all_fail_empty():
    """三服务全失败返回空列表(由上层决定是否提示)。"""
    from nonebot_plugin_parser.music_search import aggregate_search

    parser = MagicMock()
    with patch(
        "nonebot_plugin_parser.music_search.search_netease",
        new=AsyncMock(side_effect=RuntimeError("x")),
    ), patch(
        "nonebot_plugin_parser.music_search.search_qqmusic",
        new=AsyncMock(return_value=[]),
    ), patch(
        "nonebot_plugin_parser.music_search.search_kugou",
        new=AsyncMock(return_value=[]),
    ):
        merged = await aggregate_search(parser, "kw", ["netease", "qqmusic", "kugou"])
    assert merged == []


@pytest.mark.asyncio
async def test_aggregate_search_all_three_services_present_no_truncation():
    """三服务全成功时各 5 条全部展示, 不截断, 三个平台都出现。

    回归测试: 修复前 per_service_limit=5 + global_limit=10 导致网易云+QQ 已凑满 10,
    酷狗被完全截掉; 修复后去掉 global_limit 截断, 15 条全部展示。
    """
    from nonebot_plugin_parser.music_search import SearchItem, aggregate_search

    parser = MagicMock()
    netease = [SearchItem("netease", f"n{i}", f"歌{i}", "a") for i in range(5)]  # type: ignore[arg-type]
    qq = [SearchItem("qqmusic", f"q{i}", f"Q{i}", "b") for i in range(5)]  # type: ignore[arg-type]
    kugou = [SearchItem("kugou", f"k{i}", f"K{i}", "c") for i in range(5)]  # type: ignore[arg-type]
    with patch("nonebot_plugin_parser.music_search.search_netease", new=AsyncMock(return_value=netease)), patch(
        "nonebot_plugin_parser.music_search.search_qqmusic", new=AsyncMock(return_value=qq)
    ), patch("nonebot_plugin_parser.music_search.search_kugou", new=AsyncMock(return_value=kugou)):
        merged = await aggregate_search(parser, "kw", ["netease", "qqmusic", "kugou"])

    # 3 × 5 = 15 条全部展示, 不截断
    assert len(merged) == 15
    # 三个平台都出现, 顺序: 网易云 5 → QQ 5 → 酷狗 5
    platforms_present = {it.platform for it in merged}
    assert platforms_present == {"netease", "qqmusic", "kugou"}
    assert [it.platform for it in merged[:5]] == ["netease"] * 5
    assert [it.platform for it in merged[5:10]] == ["qqmusic"] * 5
    assert [it.platform for it in merged[10:]] == ["kugou"] * 5


@pytest.mark.asyncio
async def test_aggregate_search_single_service_returns_5():
    """指定单服务(par网易云 等)默认搜 5 条(与三服务时每服务条数一致)。"""
    from nonebot_plugin_parser.music_search import SearchItem, aggregate_search

    parser = MagicMock()
    captured_limit = {}

    async def _fake_netease(_p, _kw, limit):
        captured_limit["limit"] = limit
        return [SearchItem("netease", str(i), f"歌{i}", "a") for i in range(limit)]  # type: ignore[arg-type]

    with patch("nonebot_plugin_parser.music_search.search_netease", new=_fake_netease):
        merged = await aggregate_search(parser, "kw", ["netease"])

    assert captured_limit.get("limit") == 5, "单服务默认 per_service_limit=5"
    assert len(merged) == 5


@pytest.mark.asyncio
async def test_aggregate_search_dedup():
    """同 platform+song_id+name 的重复项去重。"""
    from nonebot_plugin_parser.music_search import SearchItem, aggregate_search

    parser = MagicMock()
    dup = [SearchItem("netease", "1", "同名歌", "a")]  # type: ignore[arg-type]
    with patch(
        "nonebot_plugin_parser.music_search.search_netease",
        new=AsyncMock(return_value=dup),
    ), patch(
        "nonebot_plugin_parser.music_search.search_qqmusic",
        new=AsyncMock(return_value=[]),
    ), patch(
        "nonebot_plugin_parser.music_search.search_kugou",
        new=AsyncMock(return_value=dup),  # 同样的 netease 项(模拟边界)
    ):
        merged = await aggregate_search(parser, "kw", ["netease", "qqmusic", "kugou"])
    assert len(merged) == 1



def test_search_item_display():
    """SearchItem.display 文本格式。"""
    from nonebot_plugin_parser.music_search import SearchItem

    it = SearchItem("netease", "1", "晴天", "周杰伦")  # type: ignore[arg-type]
    assert it.display == "晴天 - 周杰伦"
    assert it.platform_display == "网易云"
