"""抖音图文「逐项发送」模式（旧模式）的群组级开关。

抖音图文默认合成为单条轮播视频发送；本开关让单个群切回逐项发送：图片走
合并转发，纯静态图文额外补发一条 BGM 音频。

与 ``platform_switch`` 同构：存储 / 判定与消息匹配无关，而 parsers 层的抖音
解析器要读、``matchers/filter.py`` 的开关指令要写，parsers 层又不应反向依赖
matchers 层，故放在本中立模块。
"""

from typing import Any

from nonebot import logger
from nonebot_plugin_uninfo import Session

from .config import pconfig
from .persist import load_json_or, atomic_write_json
from .platform_switch import get_group_key

_LEGACY_GROUPS_PATH = pconfig.data_dir / "douyin_slides_legacy.json"


def load_legacy_groups() -> set[str]:
    """加载开启逐项发送模式的群组；文件不存在或损坏时返回空（全部走合成模式）。"""
    data: Any = load_json_or(_LEGACY_GROUPS_PATH, [], context="抖音图文模式持久化")
    if not isinstance(data, list):
        return set()
    return {str(x) for x in data}


def save_legacy_groups() -> None:
    """把内存中的逐项发送群组刷盘（原子写）"""
    atomic_write_json(_LEGACY_GROUPS_PATH, sorted(_LEGACY_GROUPS))


# 内存中开启逐项发送模式的群组 key 集合；只记开启的群，缺省即合成模式
_LEGACY_GROUPS: set[str] = load_legacy_groups()


def is_slides_legacy_mode(session: Session) -> bool:
    """判断当前会话是否开启了抖音图文逐项发送模式（私聊恒为合成模式）"""
    if session.scene.is_private:
        return False
    return get_group_key(session) in _LEGACY_GROUPS


def set_slides_legacy_mode(session: Session, enabled: bool) -> bool:
    """设置当前群的抖音图文逐项发送模式，返回状态是否发生变化"""
    group_key = get_group_key(session)
    if enabled == (group_key in _LEGACY_GROUPS):
        return False
    if enabled:
        _LEGACY_GROUPS.add(group_key)
    else:
        _LEGACY_GROUPS.discard(group_key)
    save_legacy_groups()
    return True


async def current_slides_legacy_mode() -> bool:
    """当前消息所在会话是否开启了抖音图文逐项发送模式。

    解析器拿不到 matcher 注入的 Session，改经 nonebot 的 ``current_bot`` /
    ``current_event`` ContextVar 构造（与 ``parsers/base.py`` 的
    ``_is_platform_allowed`` 同款）。拿不到上下文（测试、API 等非消息触发的
    调用）或构造失败时返回 False —— 回落默认的合成模式。
    """
    # 没有任何群开启时直接短路，省掉 get_session 的适配器接口调用
    if not _LEGACY_GROUPS:
        return False

    try:
        from nonebot.matcher import current_bot, current_event
        from nonebot_plugin_uninfo import get_session

        bot = current_bot.get()
        event = current_event.get()
    except LookupError:
        return False

    # get_session 会调适配器接口（如 OneBot get_group_info），可能抛 I/O 异常
    try:
        session = await get_session(bot, event)
    except Exception:
        logger.debug("current_slides_legacy_mode: get_session 失败, 按合成模式处理", exc_info=True)
        return False

    if session is None:
        return False
    return is_slides_legacy_mode(session)
