"""用户授权与黑名单。

提供「按用户 + 受控项」的细粒度授权,以及全局黑名单。授权分两层:
全局(跨群生效,白名单语义)+ 群组(本群独立),全局优先。

数据持久化到 ``data_dir/user_grants.json`` 与 ``data_dir/user_blacklist.json``,
沿用 :mod:`matchers.filter` 的同步 ``write_text`` 风格(数据量小,无需异步/锁)。

判定优先级:

1. SUPERUSER → 永远放行(且永不被拉黑,防锁死)
2. 全局黑名单 → 永远拒绝
3. 全局授权命中 → 放行
4. 群组授权命中 → 放行
5. 否则 → 拒绝
"""

import json
from pathlib import Path

from nonebot.rule import Rule
from nonebot.permission import SUPERUSER, Permission
from nonebot_plugin_uninfo import Session, UniSession

from ..config import gconfig, pconfig

# ── 受控项语义键(不含前缀,与 parser_force_prefix 解耦)─────────
# 授权/判定时统一用这些键, 不受命令前缀变化影响。
# 命令注册时通过 COMMAND_TO_ITEM 把真实命令名(含前缀)映射回语义键。

FORCE_PARSE = "强制解析"
"""前缀强制解析的虚拟语义键(非真实命令, 对应 rule.py 的 force_parse 行为)。"""

BILI_LOGIN = "bilibili登录"
"""B 站扫码登录(``blogin``)。"""

NETEASE_LOGIN = "网易云登录"
"""网易云登录/登出(``par网易云登录`` / ``parwyy登录`` 及对应登出)。"""

QQ_LOGIN = "qq登录"
"""QQ 音乐登录/登出(``parqq登录`` / ``parqq登出``)。"""

DY_TTWID = "抖音ttwid"
"""抖音 ttwid 写入/查看(``dyttwid`` / ``dyttwid查看``)。"""

DY_COOKIE = "抖音cookie"
"""抖音 cookie 写入/查看(``dycookie`` / ``dycookie查看``)。"""

#: 所有可下放的凭据语义键(用于 par授权查看 列举、参数校验等)
DELEGABLE_ITEMS: tuple[str, ...] = (
    FORCE_PARSE,
    BILI_LOGIN,
    NETEASE_LOGIN,
    QQ_LOGIN,
    DY_TTWID,
    DY_COOKIE,
)


def resolve_item(raw: str) -> str | None:
    """把用户在授权命令里输入的受控项文本归一化为语义键。

    支持以下输入(大小写/前后空白不敏感):
    - 语义键原文: ``强制解析`` / ``bilibili登录`` / ``网易云登录`` / ``qq登录`` ...
    - 真实命令名(含或不含前缀): ``parqq登录`` / ``qq登录`` / ``dycookie`` ...
    - 中文别名: ``b站登录``→bilibili登录, ``抖音``→抖音cookie(更宽松场景留扩展)

    返回 None 表示无法识别。
    """
    text = raw.strip()
    if not text:
        return None
    # 1. 直接命中语义键
    for item in DELEGABLE_ITEMS:
        if text == item:
            return item
    # 2. 命令名 → 语义键映射(覆盖各前缀形态)
    for item in COMMAND_TO_ITEM.values():
        if text == item:
            return item
    # 3. 命令名直接匹配(去前缀后查映射)
    return COMMAND_TO_ITEM.get(_strip_prefix(text))


def _strip_prefix(cmd: str) -> str:
    """去掉命令名前的 parser_force_prefix(若有)。"""
    pfx = pconfig.parse_prefix
    if pfx and cmd.startswith(pfx):
        return cmd[len(pfx) :]
    return cmd


#: 真实命令名 → 语义键 的映射。命令注册时填充(_register_command_map, 见 __init__.py)。
#: 之所以放在 auth 而非 __init__, 是为了让 resolve_item 能闭环;
#: 注册顺序保证在任意授权/判定调用前已填好。
COMMAND_TO_ITEM: dict[str, str] = {
    # 硬编码凭据命令(不拼前缀)
    "blogin": BILI_LOGIN,
    "dyttwid": DY_TTWID,
    "dyttwid查看": DY_TTWID,
    "dycookie": DY_COOKIE,
    "dycookie查看": DY_COOKIE,
}


def register_command_item(command: str, item: str) -> None:
    """登记一条「真实命令名 → 语义键」映射(供 resolve_item 反查)。

    凭据命令注册时调用, 覆盖各前缀形态(如 ``parqq登录`` / ``jxqq登录``)。
    """
    COMMAND_TO_ITEM[command] = item


# ── SUPERUSER 集合(判定时直接放行)──────────────────────────
_SUPERUSERS: set[str] = set(gconfig.superusers)


def _is_super(user_id: str) -> bool:
    return user_id in _SUPERUSERS


# ── 群组键:复用 filter 的 get_group_key,避免重复 ──────────
def _get_group_key(session: Session) -> str:
    """群组唯一标识 ``{scope}_{scene_path}``(与 filter.get_group_key 一致)。"""
    return f"{session.scope}_{session.scene_path}"


# ════════════════════════════════════════════════════════════
# 授权(grants):global + groups
# ════════════════════════════════════════════════════════════
_GRANTS_PATH: Path = pconfig.data_dir / "user_grants.json"

# 内存结构(与 JSON 文件格式一致):
#   {
#     "global": { user_id: [受控项...] | [] },   # [] = 授权全部
#     "groups": { group_key: { user_id: [...] } }
#   }
_GRANTS: dict[str, dict] = {"global": {}, "groups": {}}


def _load_grants() -> None:
    """从磁盘加载授权数据,文件不存在则按空配置初始化。"""
    global _GRANTS
    if not _GRANTS_PATH.exists():
        _GRANTS_PATH.write_text(json.dumps({"global": {}, "groups": {}}, ensure_ascii=False))
    try:
        data = json.loads(_GRANTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        data = {}
    _GRANTS = {
        "global": {str(k): list(v) if v else [] for k, v in data.get("global", {}).items()},
        "groups": {
            str(gk): {str(uid): list(v) if v else [] for uid, v in gv.items()}
            for gk, gv in data.get("groups", {}).items()
        },
    }


def _save_grants() -> None:
    """持久化授权数据到磁盘。"""
    _GRANTS_PATH.write_text(json.dumps(_GRANTS, ensure_ascii=False, indent=2))


_load_grants()


def _matches(granted: list[str] | None, item: str) -> bool:
    """granted 为 None/[] 视为「全部授权」,否则检查 item 是否在显式清单内。"""
    if not granted:  # None 或 []
        return True
    return item in granted


def is_authorized(user_id: str, item: str, session: Session) -> bool:
    """判定 ``user_id`` 能否使用受控项 ``item``。

    判定顺序: SUPERUSER → 黑名单 → 全局授权 → 群组授权 → 拒绝。
    """
    if _is_super(user_id):
        return True
    if user_id in _BLACKLIST:
        return False

    gdict = _GRANTS["global"]
    if user_id in gdict and _matches(gdict[user_id], item):
        return True

    group_grants = _GRANTS["groups"].get(_get_group_key(session), {})
    if user_id in group_grants and _matches(group_grants[user_id], item):
        return True

    return False


def is_force_parse_authorized(user_id: str, session: Session) -> bool:
    """:func:`is_authorized` 对 :data:`FORCE_PARSE` 的语义包装。"""
    return is_authorized(user_id, FORCE_PARSE, session)


def grant(user_id: str, items: list[str] | None, group_key: str | None = None) -> bool:
    """授权 ``user_id`` 使用 ``items``(None/空 = 全部)。

    Args:
        user_id: 被授权用户 id
        items: 受控项列表;None 或空列表表示「全部授权」
        group_key: 指定群组键则写入群组授权,None 写入全局授权

    Returns:
        是否产生了变更(新增或更新)
    """
    target = _GRANTS["global"] if group_key is None else _GRANTS["groups"].setdefault(group_key, {})
    new_val = list(items) if items else []  # [] = 全部
    if target.get(user_id) == new_val:
        return False
    target[user_id] = new_val
    _save_grants()
    return True


def revoke(user_id: str, items: list[str] | None = None, group_key: str | None = None) -> bool:
    """撤销 ``user_id`` 的授权。

    Args:
        user_id: 被撤销用户 id
        items: 指定受控项则只移除这些项;None 表示撤销该用户全部授权。
            若用户当前是「全部授权」状态,即使指定 items 也会撤销其全部
            (因为「全部」是开放集合,无法从中减去单项)。
        group_key: 指定群组键则操作群组授权,None 操作全局授权

    Returns:
        是否产生了变更
    """
    target = _GRANTS["global"] if group_key is None else _GRANTS["groups"].get(group_key, {})
    if user_id not in target:
        return False

    def _cleanup() -> None:
        del target[user_id]
        if group_key is not None and not target:
            _GRANTS["groups"].pop(group_key, None)

    current = target[user_id]
    # items=None(撤销全部) 或 当前为全部授权([]): 整条删除
    if items is None or not current:
        _cleanup()
        _save_grants()
        return True

    # 显式列表: 移除指定项
    items_set = set(items)
    new_items = [i for i in current if i not in items_set]
    if len(new_items) == len(current):
        return False  # 没有命中任何项
    if new_items:
        target[user_id] = new_items
    else:
        _cleanup()
    _save_grants()
    return True


def list_grants(group_key: str | None = None) -> dict[str, list[str]]:
    """返回授权字典副本。group_key 指定时只返回该群授权,否则返回全局授权。"""
    if group_key is None:
        src = _GRANTS["global"]
    else:
        src = _GRANTS["groups"].get(group_key, {})
    return {uid: list(v) for uid, v in src.items()}


# ════════════════════════════════════════════════════════════
# 黑名单(blacklist):全局封禁
# ════════════════════════════════════════════════════════════
_BLACKLIST_PATH: Path = pconfig.data_dir / "user_blacklist.json"
_BLACKLIST: set[str] = set()


def _load_blacklist() -> None:
    global _BLACKLIST
    if not _BLACKLIST_PATH.exists():
        _BLACKLIST_PATH.write_text(json.dumps([]))
    try:
        data = json.loads(_BLACKLIST_PATH.read_text())
        _BLACKLIST = {str(x) for x in data}
    except (json.JSONDecodeError, OSError):
        _BLACKLIST = set()


def _save_blacklist() -> None:
    _BLACKLIST_PATH.write_text(json.dumps(sorted(_BLACKLIST), ensure_ascii=False, indent=2))


_load_blacklist()


def is_blacklisted(user_id: str) -> bool:
    """是否在全局黑名单中(SUPERUSER 永不被拉黑,防锁死)。"""
    if _is_super(user_id):
        return False
    return user_id in _BLACKLIST


def add_blacklist(user_id: str) -> bool:
    """加入全局黑名单。SUPERUSER 不可被拉黑。返回是否新增成功。"""
    if _is_super(user_id):
        return False
    if user_id in _BLACKLIST:
        return False
    _BLACKLIST.add(user_id)
    _save_blacklist()
    return True


def remove_blacklist(user_id: str) -> bool:
    """移出全局黑名单。返回是否移除成功。"""
    if user_id not in _BLACKLIST:
        return False
    _BLACKLIST.discard(user_id)
    _save_blacklist()
    return True


def get_blacklist() -> list[str]:
    """返回黑名单(已排序副本)。"""
    return sorted(_BLACKLIST)


# ════════════════════════════════════════════════════════════
# Rule:黑名单用户不响应(挂到 bm/ym/点歌 等用户级指令)
# ════════════════════════════════════════════════════════════
async def _not_blacklisted(session: Session = UniSession()) -> bool:
    user = getattr(session, "user", None)
    if user is None:
        return True
    return not is_blacklisted(str(user.id))


def not_blacklisted() -> Rule:
    """Rule:非黑名单用户才放行。"""
    return Rule(_not_blacklisted)


# ════════════════════════════════════════════════════════════
# Permission:命令下放(SUPERUSER 或被授权用户)
# ════════════════════════════════════════════════════════════
def super_or_authorized(item: str) -> Permission:
    """构造 Permission:SUPERUSER 直接放行,否则检查是否被授权 ``item``。

    用于凭据类命令的「下放」:注册时 ``permission=super_or_authorized(QQ_LOGIN)``,
    使得被 ``par授权 @用户 qq登录`` 授权的普通用户也能触发该命令。

    SUPERUSER 通过 NoneBot 原生 ``SUPERUSER`` Permission 短路(首个命中即放行),
    非 SUPERUSER 走 ``is_authorized`` 判定(含黑名单拦截)。
    """

    async def _authorized_check(sess: Session = UniSession()) -> bool:
        user = getattr(sess, "user", None)
        if user is None:
            return False
        return is_authorized(str(user.id), item, sess)

    return SUPERUSER | Permission(_authorized_check)


def private_authorized(item: str) -> Permission:
    """构造 Permission:私聊场景下,SUPERUSER 或被授权 ``item`` 的用户放行。

    用于需要限定私聊的凭据命令(如 ``blogin``):群聊一律拒绝,私聊按授权判定。
    语义 = ``is_private AND (is_super OR is_authorized(item))``。

    注意: NoneBot 不允许 Permission 之间用 ``&``, 故把私聊检查与授权检查
    合并进同一个 check 函数, 用 ``SUPERUSER |`` 短路超管。
    """

    async def _private_authorized_check(sess: Session = UniSession()) -> bool:
        scene = getattr(sess, "scene", None)
        if scene is None or not scene.is_private:
            return False
        user = getattr(sess, "user", None)
        if user is None:
            return False
        return is_authorized(str(user.id), item, sess)

    return SUPERUSER | Permission(_private_authorized_check)
