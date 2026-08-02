"""用户授权与黑名单管理命令。

从 ``matchers/__init__.py`` 拆出。细粒度授权: 按用户 + 受控项(默认全部),
分全局(跨群)与群组(本群)两层。黑名单: 全局封禁, 命中后不解析/不响应功能指令
(SUPERUSER 不可被拉黑)。受控项语义键见 ``auth.py``, 当前生效的是
"强制解析"(前缀强制解析授权)。

本模块还导出共享 helper ``_normalize_user_id``, 供 ``tg_login.py`` 复用。
"""

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession

from . import auth
from .filter import get_group_key
from ..config import gconfig, pconfig


def _normalize_user_id(arg: str) -> str | None:
    """从命令参数中提取用户 id，支持 `@用户名`(部分适配器) 或纯数字 id。
    返回 None 表示无法识别。

    本 helper 被 admin（``_extract_target_user``）与 tg_login
    （``tg授权``/``tg取消授权``）共享, 故定义在此并由 tg_login 跨模块导入。
    """
    arg = arg.strip()
    if not arg:
        return None
    # 去掉 @ 前缀
    if arg.startswith("@"):
        arg = arg[1:]
    return arg or None


def _extract_target_user(message: Message) -> tuple[str | None, str]:
    """从命令参数中提取「被操作用户」与其余参数。

    优先取消息 at 段的 qq/user_id(更准),取不到回退到文本首段的 @用户名/纯数字。
    返回 (user_id_or_None, 剩余参数文本)。
    """
    # 1. 优先 at 段
    for seg in message["at"]:
        seg_data = getattr(seg, "data", {}) or {}
        # OneBot v11: qq; 其他适配器: user_id
        uid = seg_data.get("qq") or seg_data.get("user_id")
        if uid:
            uid = str(uid)
            # 去掉 at 段后的剩余纯文本
            rest = message.extract_plain_text().strip()
            return uid, rest

    # 2. 回退到文本: 首段作为用户标识, 其余作为受控项
    parts = message.extract_plain_text().split()
    if not parts:
        return None, ""
    uid = _normalize_user_id(parts[0])
    rest = " ".join(parts[1:])
    return uid, rest


def _format_items(items: list[str]) -> str:
    """受控项列表的可读展示: 空列表显示「全部受控项」。"""
    return "、".join(items) if items else "全部受控项"


def _parse_items(rest: str) -> list[str] | None:
    """把授权命令的「受控项」文本归一化为语义键列表。

    每个输入项通过 auth.resolve_item 归一化; 任意一项无法识别则返回 None(触发用法提示)。
    空输入返回 [](语义=授权/撤销全部)。
    """
    parts = rest.split() if rest.strip() else []
    if not parts:
        return []  # 空 = 全部
    items: list[str] = []
    for raw in parts:
        resolved = auth.resolve_item(raw)
        if resolved is None:
            return None  # 有无法识别的项, 让调用方报错
        if resolved not in items:
            items.append(resolved)
    return items


def _register_admin_commands() -> None:
    """注册授权/黑名单管理命令, 命令名跟随 parser_force_prefix(空前缀回退 par)。

    受控项参数用语义键(不含前缀), 通过 auth.resolve_item 归一化:
    用户可输入 ``强制解析`` / ``qq登录`` / ``parqq登录`` 等任意形态, 均映射回语义键。
    """
    prefix = pconfig.parse_prefix or "par"

    @on_command(f"{prefix}授权", block=True, permission=SUPERUSER).handle()
    async def _par_grant(matcher: Matcher, session: Session = UniSession(), args: Message = CommandArg()):
        """SUPERUSER: 授权用户使用受控项(本群)。用法: <prefix>授权 @用户 [受控项...]

        不写受控项 = 授权全部。本群生效(私聊场景视为全局授权)。
        受控项用语义键(如 强制解析/qq登录/网易云登录/dyttwid/dycookie/bilibili登录),
        也接受带前缀的真实命令名(如 parqq登录)。
        """
        user_id, rest = _extract_target_user(args)
        if not user_id:
            await matcher.finish(
                f"用法: {prefix}授权 @用户 [受控项...]\n(不写=授权全部; 受控项: {', '.join(auth.DELEGABLE_ITEMS)})"
            )
        items = _parse_items(rest)
        if items is None:
            await matcher.finish(f"未识别的受控项, 可用: {', '.join(auth.DELEGABLE_ITEMS)}")

        group_key = None if session.scene.is_private else get_group_key(session)
        scope = "全局" if group_key is None else "本群"
        if auth.grant(user_id, items, group_key):
            await matcher.finish(f"✅ 已{scope}授权 {user_id} 使用 {_format_items(items)}")
        await matcher.finish(f"{user_id} 的{scope}授权未变化(已是该配置)")

    @on_command(f"{prefix}全局授权", block=True, permission=SUPERUSER).handle()
    async def _par_grant_global(matcher: Matcher, args: Message = CommandArg()):
        """SUPERUSER: 全局授权用户(跨群生效)。用法: <prefix>全局授权 @用户 [受控项...]"""
        user_id, rest = _extract_target_user(args)
        if not user_id:
            await matcher.finish(f"用法: {prefix}全局授权 @用户 [受控项...]\n(不写=授权全部)")
        items = _parse_items(rest)
        if items is None:
            await matcher.finish(f"未识别的受控项, 可用: {', '.join(auth.DELEGABLE_ITEMS)}")

        if auth.grant(user_id, items, group_key=None):
            await matcher.finish(f"✅ 已全局授权 {user_id} 使用 {_format_items(items)}")
        await matcher.finish(f"{user_id} 的全局授权未变化(已是该配置)")

    @on_command(f"{prefix}取消授权", block=True, permission=SUPERUSER).handle()
    async def _par_revoke(matcher: Matcher, session: Session = UniSession(), args: Message = CommandArg()):
        """SUPERUSER: 撤销用户的授权(全局 + 本群)。用法: <prefix>取消授权 @用户 [受控项...]

        不写受控项 = 撤销该用户全部授权。
        """
        user_id, rest = _extract_target_user(args)
        if not user_id:
            await matcher.finish(f"用法: {prefix}取消授权 @用户 [受控项...]\n(不写=撤销全部)")
        items = _parse_items(rest)  # 取消授权允许空(=撤销全部)

        changed_global = auth.revoke(user_id, items, group_key=None)
        # 群组授权: 私聊触发无群组上下文, 只清全局; 群聊触发时同时清本群。
        group_key = None if session.scene.is_private else get_group_key(session)
        changed_group = auth.revoke(user_id, items, group_key=group_key) if group_key else False

        if changed_global or changed_group:
            await matcher.finish(f"✅ 已撤销 {user_id} 的授权({', '.join(items) if items else '全部'})")
        await matcher.finish(f"{user_id} 没有可撤销的授权")

    @on_command(f"{prefix}授权查看", block=True, permission=SUPERUSER).handle()
    async def _par_grants_view(matcher: Matcher, session: Session = UniSession()):
        """SUPERUSER: 查看授权名单(全局 + 本群)。"""
        lines: list[str] = []
        global_grants = auth.list_grants(group_key=None)
        if global_grants:
            lines.append("【全局授权】")
            for uid, items in global_grants.items():
                lines.append(f"  {uid}: {_format_items(items)}")
        else:
            lines.append("【全局授权】(空)")

        if not session.scene.is_private:
            group_key = get_group_key(session)
            group_grants = auth.list_grants(group_key=group_key)
            lines.append(f"【本群授权】({group_key})")
            if group_grants:
                for uid, items in group_grants.items():
                    lines.append(f"  {uid}: {_format_items(items)}")
            else:
                lines.append("  (空)")

        await matcher.finish("\n".join(lines))

    @on_command(f"{prefix}拉黑", block=True, permission=SUPERUSER).handle()
    async def _par_ban(matcher: Matcher, args: Message = CommandArg()):
        """SUPERUSER: 全局拉黑用户(不解析/不响应功能指令)。用法: <prefix>拉黑 @用户"""
        user_id, _ = _extract_target_user(args)
        if not user_id:
            await matcher.finish(f"用法: {prefix}拉黑 @用户")
        if user_id in set(gconfig.superusers):
            await matcher.finish("不可拉黑 SUPERUSER")
        if auth.add_blacklist(user_id):
            await matcher.finish(f"✅ 已拉黑 {user_id}(全局封禁: 不解析/不响应功能指令)")
        await matcher.finish(f"{user_id} 已在黑名单中")

    @on_command(f"{prefix}解除拉黑", block=True, permission=SUPERUSER).handle()
    async def _par_unban(matcher: Matcher, args: Message = CommandArg()):
        """SUPERUSER: 解除全局拉黑。用法: <prefix>解除拉黑 @用户"""
        user_id, _ = _extract_target_user(args)
        if not user_id:
            await matcher.finish(f"用法: {prefix}解除拉黑 @用户")
        if auth.remove_blacklist(user_id):
            await matcher.finish(f"✅ 已解除 {user_id} 的拉黑")
        await matcher.finish(f"{user_id} 不在黑名单中")

    @on_command(f"{prefix}黑名单", block=True, permission=SUPERUSER).handle()
    async def _par_blacklist_view(matcher: Matcher):
        """SUPERUSER: 查看全局黑名单。"""
        blacklist = auth.get_blacklist()
        if not blacklist:
            await matcher.finish("当前黑名单为空")
        await matcher.finish("全局黑名单:\n" + "\n".join(blacklist))


# 模块加载时即注册（与原 __init__.py 行为一致）
_register_admin_commands()
