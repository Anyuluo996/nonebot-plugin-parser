"""抖音图文「逐项发送」模式群级开关（douyin_slides_switch）的单元测试。

覆盖存储往返（持久化 JSON）、群隔离、私聊恒 False、无消息上下文回落合成
模式（默认）。
"""

import json
from types import SimpleNamespace
from pathlib import Path

# nonebot 插件加载依赖 conftest 先初始化, 插件内模块一律在测试函数内导入

_GROUP_KEY = "QQClient_/group/12345"


def _fake_session(scene_path: str = "/group/12345", private: bool = False):
    """Uninfo Session 的最小桩: get_group_key 用 scope/scene_path, 判定用 scene.is_private。"""
    return SimpleNamespace(
        scope="QQClient",
        scene_path=scene_path,
        scene=SimpleNamespace(is_private=private),
    )


def _patch_state(monkeypatch, tmp_path: Path, groups: set[str] | None = None):
    """把开关模块的存储路径与内存态隔离到 tmp_path, 避免污染真实 data_dir。"""
    from nonebot_plugin_parser import douyin_slides_switch as slides_switch

    monkeypatch.setattr(slides_switch, "_LEGACY_GROUPS_PATH", tmp_path / "douyin_slides_legacy.json")
    monkeypatch.setattr(slides_switch, "_LEGACY_GROUPS", set(groups) if groups else set())


def test_group_toggle_roundtrip_persists(monkeypatch, tmp_path):
    """开启 → 本群命中 + JSON 落盘; 关闭 → 移除 + 落盘; 重复设置幂等。"""
    from nonebot_plugin_parser import douyin_slides_switch as slides_switch

    _patch_state(monkeypatch, tmp_path)
    path = slides_switch._LEGACY_GROUPS_PATH
    session = _fake_session()

    assert slides_switch.is_slides_legacy_mode(session) is False
    assert slides_switch.set_slides_legacy_mode(session, True) is True, "首次开启应有状态变化"
    assert slides_switch.is_slides_legacy_mode(session) is True
    assert path.exists(), "开启后应原子写持久化文件"
    assert json.loads(path.read_text("utf-8")) == [_GROUP_KEY]

    # 其它群不受影响
    assert slides_switch.is_slides_legacy_mode(_fake_session(scene_path="/group/67890")) is False

    assert slides_switch.set_slides_legacy_mode(session, False) is True
    assert slides_switch.is_slides_legacy_mode(session) is False
    assert json.loads(path.read_text("utf-8")) == []
    # 幂等: 重复关闭无状态变化
    assert slides_switch.set_slides_legacy_mode(session, False) is False


def test_private_session_never_legacy(monkeypatch, tmp_path):
    """私聊恒走合成模式, 开关不生效。"""
    from nonebot_plugin_parser import douyin_slides_switch as slides_switch

    _patch_state(monkeypatch, tmp_path, groups={_GROUP_KEY})
    private = _fake_session(scene_path="/private/12345", private=True)

    assert slides_switch.is_slides_legacy_mode(private) is False


async def test_current_mode_without_context_defaults_to_synthesis(monkeypatch, tmp_path):
    """有群开启但无消息上下文(测试/直调) → 回落合成模式(默认)。"""
    from nonebot_plugin_parser import douyin_slides_switch as slides_switch

    _patch_state(monkeypatch, tmp_path, groups={_GROUP_KEY})
    # 不在 matcher 运行上下文, current_bot/current_event 为空 → LookupError 降级
    assert await slides_switch.current_slides_legacy_mode() is False


async def test_current_mode_empty_groups_short_circuits(monkeypatch, tmp_path):
    """没有任何群开启时直接短路, 无需解析消息上下文。"""
    from nonebot_plugin_parser import douyin_slides_switch as slides_switch

    _patch_state(monkeypatch, tmp_path)
    assert await slides_switch.current_slides_legacy_mode() is False
