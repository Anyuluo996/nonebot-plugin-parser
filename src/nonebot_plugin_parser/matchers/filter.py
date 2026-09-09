from nonebot import logger, on_command
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.adapters import Message
from nonebot.exception import MatcherException
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import ADMIN, OWNER, Session, UniSession
from nonebot_plugin_alconna.uniseg import UniMsg

from ..config import pconfig
from ..parsers import BaseParser
from ..persist import load_json_or, atomic_write_json
from ..constants import PlatformEnum

# 平台开关的存储/判定已下沉到中立模块 platform_switch（parsers 层也要用），
# 此处 re-export 保持既有导入路径 `matchers.filter.X` 兼容。
from ..platform_switch import (
    _ALL_PLATFORMS as _ALL_PLATFORMS,
)
from ..platform_switch import (
    _DISABLED_PLATFORMS_DICT as _DISABLED_PLATFORMS_DICT,
)
from ..platform_switch import (
    get_group_key as get_group_key,
)
from ..platform_switch import (
    is_platform_enabled as is_platform_enabled,
)
from ..platform_switch import (
    save_disabled_platforms as save_disabled_platforms,
)

PARSER_CONTROL_PERMISSION = SUPERUSER | OWNER() | ADMIN()

# Telegram 解析白名单：被 SUPERUSER 授权可使用 Telegram 解析的用户 id 集合
_TG_WHITELIST_PATH = pconfig.data_dir / "tg_whitelist.json"


def load_tg_whitelist() -> list[str]:
    """加载 Telegram 解析白名单；文件不存在或损坏时返回空列表。"""
    data = load_json_or(_TG_WHITELIST_PATH, [], context="Telegram 白名单持久化")
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def save_tg_whitelist() -> None:
    """保存 Telegram 解析白名单（原子写）"""
    atomic_write_json(_TG_WHITELIST_PATH, list(_TG_WHITELIST_SET))


# 内存中的 Telegram 白名单
_TG_WHITELIST_SET: set[str] = set(load_tg_whitelist())


def is_tg_authorized(user_id: str) -> bool:
    """判断指定用户是否在 Telegram 解析白名单中"""
    return str(user_id) in _TG_WHITELIST_SET


def add_tg_whitelist(user_id: str) -> bool:
    """添加用户到 Telegram 白名单，返回是否新增成功"""
    user_id = str(user_id)
    if user_id in _TG_WHITELIST_SET:
        return False
    _TG_WHITELIST_SET.add(user_id)
    save_tg_whitelist()
    return True


def remove_tg_whitelist(user_id: str) -> bool:
    """从 Telegram 白名单移除用户，返回是否移除成功"""
    user_id = str(user_id)
    if user_id not in _TG_WHITELIST_SET:
        return False
    _TG_WHITELIST_SET.discard(user_id)
    save_tg_whitelist()
    return True


def get_tg_whitelist() -> list[str]:
    """获取 Telegram 白名单（返回副本）"""
    return sorted(_TG_WHITELIST_SET)


def _starts_with_force_prefix(message: UniMsg | None) -> bool:
    parse_prefix = pconfig.parse_prefix
    if not parse_prefix or message is None:
        return False

    text = message.extract_plain_text().strip()
    # 纯前缀 (如 "par") 用于引用回复场景: 回复含 URL 的消息 + 只输入前缀,
    # 从被引用消息提取 URL 强制解析。prefix+ / prefix<空格> 为直接带 URL 的形式。
    return text == parse_prefix or text.startswith(f"{parse_prefix}+") or text.startswith(f"{parse_prefix} ")


def is_enabled(message: UniMsg, session: Session = UniSession()) -> bool:
    """判断当前会话是否启用了任意解析功能"""
    if _starts_with_force_prefix(message):
        return True

    if session.scene.is_private:
        return True

    group_key = get_group_key(session)
    disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())
    return not _ALL_PLATFORMS.issubset(disabled_platforms)


def get_platform_display_name(platform_input: str) -> str | None:
    """获取平台的显示名称

    Args:
        platform_input: 用户输入的平台名称（可以是 value 或 display_name）

    Returns:
        匹配的平台 value，如果不匹配则返回 None
    """
    # 尝试匹配枚举值
    for platform in PlatformEnum:
        if platform.value == platform_input.lower():
            return platform.value
    # 尝试匹配显示名称
    for platform in PlatformEnum:
        if platform.name.lower() == platform_input.lower():
            return platform.value

    # 中文别名映射
    chinese_aliases = {
        "b站": "bilibili",
        "B站": "bilibili",
        "抖音": "douyin",
        "微博": "weibo",
        "推特": "twitter",
        "油管": "youtube",
        "快手": "kuaishou",
        "小红书": "xiaohongshu",
        "xhs": "xiaohongshu",
        "A站": "acfun",
        "a站": "acfun",
    }
    if platform_input in chinese_aliases:
        return chinese_aliases[platform_input]

    return None


async def get_parser_class(platform_name: str) -> type[BaseParser] | None:
    """根据平台名称获取对应的 Parser 类"""
    from ..parsers import PARSERS

    return PARSERS.get(platform_name)


def check_platform_available(platform_name: str) -> bool:
    """检查指定平台是否可用（已实现 Parser）

    Args:
        platform_name: 平台名称

    Returns:
        bool: 平台是否可用
    """
    from ..parsers import PARSERS

    return platform_name in PARSERS


@on_command("开启解析", permission=PARSER_CONTROL_PERMISSION, rule=to_me(), block=True).handle()
async def enable_parser(matcher: Matcher, session: Session = UniSession(), args: Message = CommandArg()):
    """开启解析"""
    try:
        group_key = get_group_key(session)

        # 解析平台名称
        platform_name = args.extract_plain_text().strip()
        logger.debug(f"[开启解析] 原始参数: '{platform_name}', group_key: {group_key}")

        if platform_name:
            # 尝试转换为标准平台名称
            standard_name = get_platform_display_name(platform_name)
            if standard_name is None:
                await matcher.finish(f"未知的平台: {platform_name}")
            if not check_platform_available(standard_name):
                await matcher.finish(f"平台 {platform_name} 暂不支持")
            logger.debug(f"[开启解析] 转换后平台名: {standard_name}")

            # 启用指定平台
            if group_key not in _DISABLED_PLATFORMS_DICT:
                _DISABLED_PLATFORMS_DICT[group_key] = set()
            _DISABLED_PLATFORMS_DICT[group_key].discard(standard_name)
            if not _DISABLED_PLATFORMS_DICT[group_key]:
                del _DISABLED_PLATFORMS_DICT[group_key]
            save_disabled_platforms()
            await matcher.finish(f"{platform_name} 解析已开启")
        else:
            # 启用所有平台
            if group_key in _DISABLED_PLATFORMS_DICT:
                del _DISABLED_PLATFORMS_DICT[group_key]
                save_disabled_platforms()
            await matcher.finish("解析已开启")
    except MatcherException:
        # NoneBot 控制流异常（Finished/Rejected/Paused 等）必须放行, 不能吞成"发生错误"
        raise
    except Exception as e:
        logger.exception(f"[开启解析] 发生异常: {e}")
        await matcher.finish(f"发生错误: {e}")


@on_command("关闭解析", permission=PARSER_CONTROL_PERMISSION, rule=to_me(), block=True).handle()
async def disable_parser(matcher: Matcher, session: Session = UniSession(), args: Message = CommandArg()):
    """关闭解析"""
    try:
        group_key = get_group_key(session)
        logger.debug(f"[关闭解析] group_key: {group_key}, is_private: {session.scene.is_private}")

        # 解析平台名称
        platform_name = args.extract_plain_text().strip()
        logger.debug(f"[关闭解析] 原始参数: '{platform_name}', group_key: {group_key}")

        if platform_name:
            # 尝试转换为标准平台名称
            standard_name = get_platform_display_name(platform_name)
            if standard_name is None:
                await matcher.finish(f"未知的平台: {platform_name}")
            if not check_platform_available(standard_name):
                await matcher.finish(f"平台 {platform_name} 暂不支持")
            logger.debug(f"[关闭解析] 转换后平台名: {standard_name}")

            # 禁用指定平台
            if group_key not in _DISABLED_PLATFORMS_DICT:
                _DISABLED_PLATFORMS_DICT[group_key] = set()
            _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)
            save_disabled_platforms()
            await matcher.finish(f"{platform_name} 解析已关闭")
        else:
            # 禁用所有平台
            _DISABLED_PLATFORMS_DICT[group_key] = _ALL_PLATFORMS.copy()
            save_disabled_platforms()
            await matcher.finish("解析已关闭")
    except MatcherException:
        # NoneBot 控制流异常（Finished/Rejected/Paused 等）必须放行, 不能吞成"发生错误"
        raise
    except Exception as e:
        logger.exception(f"[关闭解析] 发生异常: {e}")
        await matcher.finish(f"发生错误: {e}")


@on_command("解析状态", permission=PARSER_CONTROL_PERMISSION, rule=to_me(), block=True).handle()
async def parser_status(matcher: Matcher, session: Session = UniSession()):
    """查询当前解析状态"""
    group_key = get_group_key(session)

    if session.scene.is_private:
        await matcher.finish("私聊模式下解析已全局开启")

    disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())

    if not disabled_platforms:
        await matcher.finish("当前群组解析已全局开启")

    # 获取所有可用平台
    enabled_platforms = _ALL_PLATFORMS - disabled_platforms

    if enabled_platforms:
        enabled_list = ", ".join(sorted(enabled_platforms))
        await matcher.finish(f"当前已开启的平台: {enabled_list}")
    else:
        await matcher.finish("当前群组解析已关闭")
