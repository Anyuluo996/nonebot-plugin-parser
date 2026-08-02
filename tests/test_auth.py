"""matchers.auth 的单元测试。

覆盖授权判定优先级(SUPERUSER > 黑名单 > 全局 > 群组)、grant/revoke、
黑名单增删查、以及持久化文件生成。隔离测试数据, 测试后清理。

注意: 所有对 ``nonebot_plugin_parser`` 的 import 都放在 fixture / 测试函数内部,
确保在 conftest 的 ``init_nonebot`` session fixture 加载完插件之后再执行
(localstore 依赖调用者插件检测, 顶层 import 会先于 fixture 触发, 导致加载失败)。
"""

import json

import pytest


class _Scene:
    def __init__(self, is_private: bool = False):
        self.is_private = is_private


class _User:
    def __init__(self, uid: str):
        self.id = uid


class _Session:
    """最小 Session stub, 模拟 uninfo.Session 的接口。"""

    def __init__(self, uid: str, scope: str = "qq", scene_path: str = "g1", is_private: bool = False):
        self.scope = scope
        self.scene_path = scene_path
        self.scene = _Scene(is_private)
        self.user = _User(uid)


@pytest.fixture
def auth_mod():
    """延迟加载 auth 模块 + filter.get_group_key。"""
    from nonebot_plugin_parser.matchers import auth
    from nonebot_plugin_parser.matchers.filter import get_group_key

    return auth, get_group_key


@pytest.fixture
def isolated_auth(auth_mod, monkeypatch, tmp_path):
    """每个测试用独立 tmp 目录 + 空数据 + 固定超管集, 避免相互污染。"""
    auth, get_group_key = auth_mod
    monkeypatch.setattr(auth, "_GRANTS_PATH", tmp_path / "user_grants.json")
    monkeypatch.setattr(auth, "_BLACKLIST_PATH", tmp_path / "user_blacklist.json")
    monkeypatch.setattr(auth, "_GRANTS", {"global": {}, "groups": {}})
    monkeypatch.setattr(auth, "_BLACKLIST", set())
    # 固定超管集合为 {"100"}, 不依赖 .env.test 是否配置 SUPERUSERS
    monkeypatch.setattr(auth, "_SUPERUSERS", {"100"})
    (tmp_path / "user_grants.json").write_text(json.dumps({"global": {}, "groups": {}}))
    (tmp_path / "user_blacklist.json").write_text(json.dumps([]))
    return auth, get_group_key


def _gkey(get_group_key, sess: _Session) -> str:
    return get_group_key(sess)  # type: ignore[arg-type]


# ── SUPERUSER 优先级 ────────────────────────────────────────
def test_superuser_always_authorized(isolated_auth):
    """SUPERUSER 永远放行(防锁死)。"""
    auth, _ = isolated_auth
    sess = _Session("100")
    assert auth.is_authorized("100", auth.FORCE_PARSE, sess) is True  # type: ignore[arg-type]
    assert auth.is_blacklisted("100") is False


def test_superuser_cannot_be_blacklisted(isolated_auth):
    auth, _ = isolated_auth
    assert auth.add_blacklist("100") is False  # 不可拉黑
    assert auth.get_blacklist() == []


# ── 黑名单优先于授权 ────────────────────────────────────────
def test_blacklist_overrides_grant(isolated_auth):
    auth, _ = isolated_auth
    sess = _Session("50")
    auth.grant("50", None)  # 全局全部授权
    auth.add_blacklist("50")
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is False  # type: ignore[arg-type]


def test_blacklist_add_remove(isolated_auth):
    auth, _ = isolated_auth
    assert auth.add_blacklist("50") is True
    assert auth.add_blacklist("50") is False  # 重复添加
    assert auth.is_blacklisted("50") is True
    assert auth.remove_blacklist("50") is True
    assert auth.remove_blacklist("50") is False  # 重复移除
    assert auth.is_blacklisted("50") is False


# ── 授权: 全局 ──────────────────────────────────────────────
def test_global_grant_all(isolated_auth):
    auth, _ = isolated_auth
    sess = _Session("50")
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is False  # type: ignore[arg-type]
    auth.grant("50", None)  # 全局全部
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is True  # type: ignore[arg-type]
    assert auth.is_authorized("50", "parqq登录", sess) is True  # type: ignore[arg-type]


def test_global_grant_specific_items(isolated_auth):
    auth, _ = isolated_auth
    sess = _Session("50")
    auth.grant("50", ["parqq登录"])
    assert auth.is_authorized("50", "parqq登录", sess) is True  # type: ignore[arg-type]
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is False  # type: ignore[arg-type]


def test_global_grant_no_change_returns_false(isolated_auth):
    auth, _ = isolated_auth
    auth.grant("50", ["parqq登录"])
    assert auth.grant("50", ["parqq登录"]) is False  # 无变化


# ── 授权: 群组 ──────────────────────────────────────────────
def test_group_grant_isolated_per_group(isolated_auth):
    auth, get_group_key = isolated_auth
    sess_g1 = _Session("50", scene_path="g1")
    sess_g2 = _Session("50", scene_path="g2")
    auth.grant("50", [auth.FORCE_PARSE], group_key=_gkey(get_group_key, sess_g1))
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess_g1) is True  # type: ignore[arg-type]
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess_g2) is False  # type: ignore[arg-type]


def test_global_priority_over_group(isolated_auth):
    """全局授权生效时即放行(全局优先, 白名单语义)。"""
    auth, _ = isolated_auth
    sess = _Session("50")
    auth.grant("50", None)  # 全局全部
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is True  # type: ignore[arg-type]


# ── revoke ─────────────────────────────────────────────────
def test_revoke_all(isolated_auth):
    auth, _ = isolated_auth
    sess = _Session("50")
    auth.grant("50", None)
    assert auth.revoke("50") is True
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is False  # type: ignore[arg-type]


def test_revoke_specific_item_from_list(isolated_auth):
    auth, _ = isolated_auth
    sess = _Session("50")
    auth.grant("50", [auth.FORCE_PARSE, "parqq登录"])
    assert auth.revoke("50", ["parqq登录"]) is True
    assert auth.is_authorized("50", "parqq登录", sess) is False  # type: ignore[arg-type]
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is True  # type: ignore[arg-type]


def test_revoke_last_item_clears_entry(isolated_auth):
    auth, _ = isolated_auth
    auth.grant("50", [auth.FORCE_PARSE])
    auth.revoke("50", [auth.FORCE_PARSE])
    assert auth.list_grants() == {}


def test_revoke_nonexistent_user(isolated_auth):
    auth, _ = isolated_auth
    assert auth.revoke("999") is False


def test_revoke_nonexistent_item(isolated_auth):
    auth, _ = isolated_auth
    auth.grant("50", [auth.FORCE_PARSE])
    assert auth.revoke("50", ["不存在的项"]) is False  # 无命中


def test_revoke_from_all_grant_also_clears(isolated_auth):
    """当前是「全部授权」状态, revoke 指定项也撤销其全部(全部是开放集合)。"""
    auth, _ = isolated_auth
    sess = _Session("50")
    auth.grant("50", None)  # 全部
    auth.revoke("50", ["parqq登录"])  # 撤销单项, 但全部授权无法减
    assert auth.is_authorized("50", auth.FORCE_PARSE, sess) is False  # type: ignore[arg-type]


# ── 持久化 ──────────────────────────────────────────────────
def test_grant_persists_to_file(isolated_auth, tmp_path):
    auth, _ = isolated_auth
    auth.grant("50", [auth.FORCE_PARSE])
    data = json.loads((tmp_path / "user_grants.json").read_text())
    assert data["global"]["50"] == [auth.FORCE_PARSE]


def test_blacklist_persists_to_file(isolated_auth, tmp_path):
    auth, _ = isolated_auth
    auth.add_blacklist("50")
    data = json.loads((tmp_path / "user_blacklist.json").read_text())
    assert "50" in data


# ── list_grants ────────────────────────────────────────────
def test_list_grants_global(isolated_auth):
    auth, _ = isolated_auth
    auth.grant("50", ["a"])
    auth.grant("60", None)
    assert auth.list_grants() == {"50": ["a"], "60": []}


def test_list_grants_group(isolated_auth):
    auth, get_group_key = isolated_auth
    sess = _Session("50")
    auth.grant("50", ["a"], group_key=_gkey(get_group_key, sess))
    assert auth.list_grants(group_key=_gkey(get_group_key, sess)) == {"50": ["a"]}
    # 全局仍为空
    assert auth.list_grants() == {}
