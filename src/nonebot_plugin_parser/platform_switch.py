"""群组级平台开关（关闭解析）的存储与判定。

从 ``matchers/filter.py`` 下沉而来：短链跨 parser 路由（``parsers/base.py`` 的
``parse_with_redirect``）也要复用 ``is_platform_enabled`` 判定，而 parsers 层不应
反向依赖 matchers 层，故把与消息匹配无关的存储 / 判定逻辑放在本中立模块；
``matchers/filter.py`` re-export 这些符号并保留命令处理器等 matcher 职责。
"""

from typing import Any

from nonebot_plugin_uninfo import Session

from .config import pconfig
from .persist import load_json_or, atomic_write_json
from .constants import PlatformEnum

_DISABLED_PLATFORMS_PATH = pconfig.data_dir / "disabled_platforms.json"
_ALL_PLATFORMS = {platform.value for platform in PlatformEnum}


def load_disabled_platforms() -> dict[str, set[str]]:
    """加载关闭解析的配置；文件不存在或损坏时返回空（解析全部开启）。"""
    data: Any = load_json_or(_DISABLED_PLATFORMS_PATH, {}, context="平台开关持久化")
    if not isinstance(data, dict):
        return {}
    # 单个群的数据损坏(值非 list)时跳过该群, 不让坏数据炸掉插件加载
    return {k: set(v) for k, v in data.items() if isinstance(v, list)}


def save_disabled_platforms() -> None:
    """把内存中的关闭解析配置刷盘（原子写）"""
    serialized = {k: list(v) for k, v in _DISABLED_PLATFORMS_DICT.items()}
    atomic_write_json(_DISABLED_PLATFORMS_PATH, serialized)


# 内存中关闭解析的配置，格式: {group_key: set(platform_names)}
_DISABLED_PLATFORMS_DICT: dict[str, set[str]] = load_disabled_platforms()


def get_group_key(session: Session) -> str:
    """获取群组的唯一标识符

    由平台名称和会话场景 ID 组成，例如 `QQClient_123456789`。
    """
    return f"{session.scope}_{session.scene_path}"


def is_platform_enabled(session: Session, platform_name: str) -> bool:
    """判断指定平台在当前会话中是否启用

    Args:
        session: 会话信息
        platform_name: 平台名称

    Returns:
        bool: 平台是否启用
    """
    if session.scene.is_private:
        return True

    group_key = get_group_key(session)
    disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())
    return platform_name not in disabled_platforms


def migrate_old_data() -> None:
    """迁移旧版本的禁用群组数据（disabled_groups.json → disabled_platforms.json）"""
    old_path = pconfig.data_dir / "disabled_groups.json"
    if not old_path.exists():
        return
    old_data: Any = load_json_or(old_path, [], context="旧版禁用群组数据迁移")
    if isinstance(old_data, list) and old_data:
        # 旧数据语义为整群禁用 → 迁移为禁用所有平台
        for group_key in old_data:
            _DISABLED_PLATFORMS_DICT[str(group_key)] = _ALL_PLATFORMS.copy()
        save_disabled_platforms()
    old_path.unlink()


# 在模块加载时执行迁移
migrate_old_data()
