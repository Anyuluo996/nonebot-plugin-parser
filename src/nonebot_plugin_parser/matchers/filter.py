import json
from pathlib import Path

from nonebot import on_command
from nonebot.rule import to_me
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot_plugin_uninfo import ADMIN, Session, UniSession

from ..config import pconfig
from ..parsers import BaseParser
from ..constants import PlatformEnum

_DISABLED_PLATFORMS_PATH: Path = pconfig.data_dir / "disabled_platforms.json"


def load_or_initialize_dict() -> dict[str, set[str]]:
    """加载或初始化关闭解析的配置

    Returns:
        dict[str, set[str]]: 群组标识 -> 禁用的平台名称集合
    """
    if not _DISABLED_PLATFORMS_PATH.exists():
        _DISABLED_PLATFORMS_PATH.write_text(json.dumps({}))
    data = json.loads(_DISABLED_PLATFORMS_PATH.read_text())
    return {k: set(v) for k, v in data.items()}


def save_disabled_platforms():
    """保存关闭解析的配置"""
    data = {k: list(v) for k, v in _DISABLED_PLATFORMS_DICT.items()}
    _DISABLED_PLATFORMS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# 内存中关闭解析的配置，格式: {group_key: set(platform_names)}
_DISABLED_PLATFORMS_DICT: dict[str, set[str]] = load_or_initialize_dict()
# 兼容旧版本的禁用群组列表
_DISABLED_GROUPS_SET: set[str] = set()


def migrate_old_data():
    """迁移旧版本的禁用群组数据"""
    old_path = pconfig.data_dir / "disabled_groups.json"
    if old_path.exists():
        old_data = set(json.loads(old_path.read_text()))
        if old_data:
            # 将旧数据迁移到新格式，标记为禁用所有平台
            all_platforms = {p.value for p in PlatformEnum}
            for group_key in old_data:
                _DISABLED_PLATFORMS_DICT[group_key] = all_platforms
            save_disabled_platforms()
            # 删除旧文件
            old_path.unlink()


# 在模块加载时执行迁移
migrate_old_data()


def get_group_key(session: Session) -> str:
    """获取群组的唯一标识符

    由平台名称和会话场景 ID 组成，例如 `QQClient_123456789`。
    """
    return f"{session.scope}_{session.scene_path}"


def is_enabled(session: Session = UniSession()) -> bool:
    """判断当前会话是否启用了任意解析功能

    只要有一个平台未禁用，就返回 True
    """
    if session.scene.is_private:
        return True

    group_key = get_group_key(session)
    disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())

    # 如果没有禁用任何平台，或者禁用的平台数少于总平台数，说明有平台可用
    all_platforms = {p.value for p in PlatformEnum}
    return len(disabled_platforms) < len(all_platforms)


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

    # 尝试匹配显示名称（中文名称）
    platform_map = {
        "acfun": "acfun",
        "a站": "acfun",
        "bilibili": "bilibili",
        "b站": "bilibili",
        "douyin": "douyin",
        "抖音": "douyin",
        "kuaishou": "kuaishou",
        "快手": "kuaishou",
        "nga": "nga",
        "tiktok": "tiktok",
        "推特": "twitter",
        "twitter": "twitter",
        "weibo": "weibo",
        "微博": "weibo",
        "xiaohongshu": "xiaohongshu",
        "小红书": "xiaohongshu",
        "xhs": "xiaohongshu",
        "youtube": "youtube",
        "油管": "youtube",
        "yt": "youtube",
        "youtu": "youtube",
    }
    return platform_map.get(platform_input.lower())


def get_enabled_platforms(session: Session) -> list[str]:
    """获取当前群组启用的平台列表

    Args:
        session: 会话信息

    Returns:
        启用的平台名称列表
    """
    group_key = get_group_key(session)
    disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())

    all_platforms = []
    for platform in PlatformEnum:
        if platform.value not in disabled_platforms:
            all_platforms.append(platform.value)

    return all_platforms


# ==================== 指令定义 ====================

@on_command("开启解析", aliases={"开启解析功能"}, rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession(), args: Message = CommandArg()):
    """开启解析

    用法:
        开启解析              # 开启所有平台的解析
        开启解析 bilibili    # 开开指定平台的解析
    """
    group_key = get_group_key(session)
    arg_text = args.extract_plain_text().strip()

    if not arg_text:
        # 开启所有平台
        if group_key in _DISABLED_PLATFORMS_DICT:
            del _DISABLED_PLATFORMS_DICT[group_key]
            save_disabled_platforms()
        await matcher.finish("已开启所有平台的解析功能")
    else:
        # 开启指定平台
        platform_value = get_platform_display_name(arg_text)
        if not platform_value:
            await matcher.finish(f"未识别的平台: {arg_text}\n"
                              f"支持的平台: {', '.join(p.value for p in PlatformEnum)}")

        disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())
        if platform_value in disabled_platforms:
            disabled_platforms.remove(platform_value)
            if not disabled_platforms:
                del _DISABLED_PLATFORMS_DICT[group_key]
            save_disabled_platforms()
            await matcher.finish(f"已开启 {platform_value} 平台的解析功能")
        else:
            await matcher.finish(f"{platform_value} 平台解析功能已开启，无需重复开启")


@on_command("关闭解析", aliases={"关闭解析功能"}, rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession(), args: Message = CommandArg()):
    """关闭解析

    用法:
        关闭解析              # 关闭所有平台的解析
        关闭解析 bilibili    # 关闭指定平台的解析
    """
    group_key = get_group_key(session)
    arg_text = args.extract_plain_text().strip()

    if not arg_text:
        # 关闭所有平台
        all_platforms = {p.value for p in PlatformEnum}
        _DISABLED_PLATFORMS_DICT[group_key] = all_platforms
        save_disabled_platforms()
        await matcher.finish("已关闭所有平台的解析功能")
    else:
        # 关闭指定平台
        platform_value = get_platform_display_name(arg_text)
        if not platform_value:
            await matcher.finish(f"未识别的平台: {arg_text}\n"
                              f"支持的平台: {', '.join(p.value for p in PlatformEnum)}")

        disabled_platforms = _DISABLED_PLATFORMS_DICT.setdefault(group_key, set())
        if platform_value not in disabled_platforms:
            disabled_platforms.add(platform_value)
            save_disabled_platforms()
            await matcher.finish(f"已关闭 {platform_value} 平台的解析功能")
        else:
            await matcher.finish(f"{platform_value} 平台解析功能已关闭，无需重复关闭")


@on_command("解析状态", rule=to_me(), permission=SUPERUSER | ADMIN(), block=True).handle()
async def _(matcher: Matcher, session: Session = UniSession()):
    """查看当前群组的解析状态"""
    if session.scene.is_private:
        await matcher.finish("私聊中默认启用所有平台解析")

    group_key = get_group_key(session)
    disabled_platforms = _DISABLED_PLATFORMS_DICT.get(group_key, set())

    # 获取平台显示名称映射
    all_parsers = BaseParser.get_all_subclass()
    platform_display_map = {}
    for parser_cls in all_parsers:
        platform_display_map[parser_cls.platform.name] = parser_cls.platform.display_name

    # 构建状态消息
    lines = ["当前群组解析状态:"]
    for platform in PlatformEnum:
        display_name = platform_display_map.get(platform.value, platform.value)
        status = "✅ 启用" if platform.value not in disabled_platforms else "❌ 禁用"
        lines.append(f"  {status} - {display_name} ({platform.value})")

    enabled_count = sum(1 for p in PlatformEnum if p.value not in disabled_platforms)
    lines.append(f"\n总计: {enabled_count}/{len(PlatformEnum)} 个平台已启用")

    await matcher.finish("\n".join(lines))
