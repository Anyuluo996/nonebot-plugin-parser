"""点歌选择状态存储单元测试（纯逻辑,无网络）。

覆盖:
- 存取基本流程
- 窗口期过期清理
- 用户隔离(A 的候选不影响 B)
- 场景隔离(同用户私聊/群各自独立)
- 覆盖语义(重新点歌覆盖旧候选)

注意: nonebot_plugin_parser 在模块顶层 require localstore,必须在测试函数内导入
(此时 conftest 的 init_nonebot fixture 已完成 NoneBot 初始化)。
"""

import time

import pytest


@pytest.fixture(autouse=True)
def _order_modules():
    """每个测试内导入,确保 NoneBot 已初始化。"""
    from nonebot_plugin_parser.music_order import (
        ORDER_EXPIRES_SECONDS,
        ORDER_STORE,
        OrderSession,
    )
    from nonebot_plugin_parser.music_search import SearchItem

    return {
        "ORDER_STORE": ORDER_STORE,
        "OrderSession": OrderSession,
        "ORDER_EXPIRES_SECONDS": ORDER_EXPIRES_SECONDS,
        "SearchItem": SearchItem,
    }


def _make_item(SearchItem, platform: str = "netease", song_id: str = "1", name: str = "歌"):
    return SearchItem(platform=platform, song_id=song_id, name=name, artist="歌手")  # type: ignore[arg-type]


def test_save_and_get(_order_modules):
    """正常存取。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    SearchItem = _order_modules["SearchItem"]
    ORDER_STORE.clear_all()
    items = [_make_item(SearchItem, song_id=str(i), name=f"歌{i}") for i in range(3)]
    ORDER_STORE.save("u1", "s1", items, keyword="周杰伦")

    got = ORDER_STORE.get("u1", "s1")
    assert got is not None
    assert len(got.items) == 3
    assert got.keyword == "周杰伦"
    assert got.items[0].name == "歌0"
    ORDER_STORE.clear_all()


def test_get_nonexistent(_order_modules):
    """未存过的会话返回 None。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    ORDER_STORE.clear_all()
    assert ORDER_STORE.get("nobody", "nowhere") is None


def test_expired_cleanup(_order_modules):
    """过期会话返回 None 并被清理。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    ORDER_EXPIRES_SECONDS = _order_modules["ORDER_EXPIRES_SECONDS"]
    SearchItem = _order_modules["SearchItem"]
    ORDER_STORE.clear_all()
    ORDER_STORE.save("u1", "s1", [_make_item(SearchItem)], keyword="kw")
    # 模拟时间过了窗口期 + 1 秒: 直接构造一个过期 session
    future_created = time.time() - ORDER_EXPIRES_SECONDS - 1
    ORDER_STORE._sessions["u1@s1"].created_at = future_created
    assert ORDER_STORE.get("u1", "s1") is None
    # 确认已被清理
    assert "u1@s1" not in ORDER_STORE._sessions


def test_user_isolation(_order_modules):
    """A 的候选不影响 B。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    SearchItem = _order_modules["SearchItem"]
    ORDER_STORE.clear_all()
    ORDER_STORE.save("alice", "s1", [_make_item(SearchItem, name="A的歌")], keyword="kw")
    ORDER_STORE.save("bob", "s1", [_make_item(SearchItem, name="B的歌")], keyword="kw")

    a = ORDER_STORE.get("alice", "s1")
    b = ORDER_STORE.get("bob", "s1")
    assert a is not None and b is not None
    assert a.items[0].name == "A的歌"
    assert b.items[0].name == "B的歌"
    ORDER_STORE.clear_all()


def test_scene_isolation(_order_modules):
    """同用户在不同场景(私聊/群)各自独立。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    SearchItem = _order_modules["SearchItem"]
    ORDER_STORE.clear_all()
    ORDER_STORE.save("u1", "private", [_make_item(SearchItem, name="私聊歌")], keyword="kw")
    ORDER_STORE.save("u1", "group1", [_make_item(SearchItem, name="群歌")], keyword="kw")

    p = ORDER_STORE.get("u1", "private")
    g = ORDER_STORE.get("u1", "group1")
    assert p is not None and g is not None
    assert p.items[0].name == "私聊歌"
    assert g.items[0].name == "群歌"
    ORDER_STORE.clear_all()


def test_overwrite_semantics(_order_modules):
    """同 user@scene 再次保存覆盖旧候选。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    SearchItem = _order_modules["SearchItem"]
    ORDER_STORE.clear_all()
    ORDER_STORE.save("u1", "s1", [_make_item(SearchItem, name="旧")], keyword="kw1")
    ORDER_STORE.save("u1", "s1", [_make_item(SearchItem, name="新")], keyword="kw2")

    got = ORDER_STORE.get("u1", "s1")
    assert got is not None
    assert len(got.items) == 1
    assert got.items[0].name == "新"
    assert got.keyword == "kw2"
    ORDER_STORE.clear_all()


def test_clear(_order_modules):
    """主动清理。"""
    ORDER_STORE = _order_modules["ORDER_STORE"]
    SearchItem = _order_modules["SearchItem"]
    ORDER_STORE.clear_all()
    ORDER_STORE.save("u1", "s1", [_make_item(SearchItem)], keyword="kw")
    assert ORDER_STORE.get("u1", "s1") is not None
    ORDER_STORE.clear("u1", "s1")
    assert ORDER_STORE.get("u1", "s1") is None


def test_order_session_is_expired(_order_modules):
    """OrderSession.is_expired 时间判断。"""
    OrderSession = _order_modules["OrderSession"]
    ORDER_EXPIRES_SECONDS = _order_modules["ORDER_EXPIRES_SECONDS"]
    sess = OrderSession(items=[], keyword="kw", created_at=time.time())
    assert not sess.is_expired()
    assert sess.is_expired(now=time.time() + ORDER_EXPIRES_SECONDS + 1)
