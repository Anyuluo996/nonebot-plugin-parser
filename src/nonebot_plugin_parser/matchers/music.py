"""点歌功能：搜索 + 序号选择。

从 ``matchers/__init__.py`` 拆出。

触发:
- ``<prefix>点歌 <歌名>`` (三服务并发)
- ``<prefix>网易云`` / ``<prefix>wyy`` / ``<prefix>qq`` / ``<prefix>酷狗`` / ``<prefix>kg`` <歌名> (指定服务)
- ``<prefix><序号>`` (选择, 窗口期 5 分钟 + 用户隔离)

``<prefix>`` = ``parser_force_prefix``, 未配置默认 ``par``。

依赖 ``__init__`` 的 ``_send_parse_result`` / ``get_parser_by_type``:
为避免 ``__init__`` ↔ ``music`` 循环导入, 这里用**函数级懒导入**取用。
"""

import re

from nonebot import logger, on_command, on_message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher, current_event
from nonebot.adapters import Message
from nonebot_plugin_uninfo import Session, UniSession

from . import auth
from .. import music_order, music_render, music_search
from ..config import pconfig
from ..helper import UniHelper, UniMessage
from ..parsers import NCMParser, BaseParser, KuGouParser
from ..exception import TipException

# 服务别名 → platform 标识 (网易云/wyy 同义, 酷狗/kg 同义, qq 唯一)
_SERVICE_ALIASES: dict[str, music_search.PlatformName] = {
    "网易云": "netease",
    "wyy": "netease",
    "qq": "qqmusic",
    "酷狗": "kugou",
    "kg": "kugou",
}
# 点歌搜索时默认并发的三个服务 (顺序即合并后的展示顺序)
_DEFAULT_ORDER_PLATFORMS: list[music_search.PlatformName] = ["netease", "qqmusic", "kugou"]


def _get_music_parser(platform: music_search.PlatformName) -> BaseParser:
    """按 platform 取已注册的音乐 Parser 实例。

    QQ 音乐在缺包时未注册,会抛 ``ValueError``（由调用方捕获转为 TipException）。
    """
    # 懒导入避免与 matchers/__init__ 循环
    from . import get_parser_by_type

    if platform == "netease":
        return get_parser_by_type(NCMParser)
    if platform == "kugou":
        return get_parser_by_type(KuGouParser)
    if platform == "qqmusic":
        from ..parsers import _QQMUSIC_AVAILABLE

        if not _QQMUSIC_AVAILABLE:
            raise TipException("搜索失败,请稍后重试")
        from ..parsers import QQMusicParser

        return get_parser_by_type(QQMusicParser)
    raise TipException("搜索失败,请稍后重试")


async def _do_music_search(
    matcher: Matcher,
    session: Session,
    keyword: str,
    platforms: list[music_search.PlatformName],
) -> None:
    """执行搜索 → 存候选 → 渲染图片 → 发送提示。

    失败处理遵循「不向用户暴露服务级诊断」原则:
    - 默认三服务并发: 单服务失败静默, 由其他服务补齐; 全失败才提示。
    - 指定服务: 失败直接提示「搜索失败,请稍后重试」。
    """
    # 懒导入避免与 matchers/__init__ 循环
    from . import get_parser_by_type

    keyword = keyword.strip()
    if not keyword:
        await matcher.finish(f"请输入要搜索的歌曲名,如「{pconfig.parse_prefix or 'par'}点歌 周杰伦」")

    # 用任意已注册 Parser 复用 request 封装发 HTTP (这里借 NCMParser 实例)
    http_parser = get_parser_by_type(NCMParser)
    items = await music_search.aggregate_search(http_parser, keyword, platforms)

    if not items:
        # 统一提示, 不暴露哪个服务失败 / 原因
        await matcher.finish("搜索失败,请稍后重试")

    # 存候选 (用户 + 场景隔离)
    user_id = session.user.id if session.user else "unknown"
    scene_id = session.scene.id if session.scene else "unknown"
    music_order.ORDER_STORE.save(user_id, scene_id, items, keyword)

    # 渲染候选列表图
    try:
        png = await music_render.render_search_list_image_async(keyword, items)
    except Exception:
        logger.exception("点歌列表渲染失败")
        await matcher.finish("搜索失败,请稍后重试")
        return

    prefix = pconfig.parse_prefix or "par"
    tip = f"发送「{prefix}序号」选择歌曲（如 {prefix}1）"
    await UniMessage(UniHelper.img_seg(png)).send()
    await UniMessage(tip).send()
    await matcher.finish()


# —— 点歌与指定服务命令注册 ——
# 命令名 = <prefix>+别名, 启动时按配置的 prefix 动态生成
def _register_music_order_commands() -> None:
    prefix = pconfig.parse_prefix or "par"

    _nb_rule = auth.not_blacklisted()

    # 默认三服务点歌
    @on_command(f"{prefix}点歌", priority=2, block=True, rule=_nb_rule).handle()
    async def _order_default(matcher: Matcher, args: Message = CommandArg(), session: Session = UniSession()):
        await _do_music_search(matcher, session, args.extract_plain_text(), _DEFAULT_ORDER_PLATFORMS)

    # 指定服务: 每个别名一个命令
    for alias, platform in _SERVICE_ALIASES.items():
        # 闭包捕获当前 platform (避免循环变量陷阱)
        def _make_handler(plat: music_search.PlatformName):
            async def _handler(matcher: Matcher, args: Message = CommandArg(), session: Session = UniSession()):
                await _do_music_search(matcher, session, args.extract_plain_text(), [plat])

            return _handler

        on_command(f"{prefix}{alias}", priority=2, block=True, rule=_nb_rule).handle()(_make_handler(platform))


# 模块加载时即注册（与原 __init__.py 行为一致）
_register_music_order_commands()


# —— 选择命令 <prefix><序号> ——
def _build_selection_regex() -> re.Pattern[str]:
    r"""构造序号选择匹配: ^<prefix>\d{1,2}$"""
    prefix = pconfig.parse_prefix or "par"
    # 转义 prefix 中的正则元字符
    return re.compile(rf"^{re.escape(prefix)}(\d{{1,2}})$")


@on_message(priority=2, block=False).handle()
async def _music_select(matcher: Matcher, session: Session = UniSession()):
    r"""``par<序号>`` 选择点歌结果。

    priority=2 高于 force-parse keyword_regex matcher, 仅当:
    1) 消息精确匹配 ``^<prefix>\d{1,2}$``
    2) 该用户在该场景下有未过期的点歌候选
    时消费; 否则 ``matcher.stop_propagation`` 不调用, 让消息继续流到 force-parse。
    """
    # 懒导入避免与 matchers/__init__ 循环（_send_parse_result 在 __init__ 中定义）
    from . import _send_parse_result

    event = current_event.get()
    text = event.get_plaintext().strip() if hasattr(event, "get_plaintext") else ""
    if not text:
        return

    pat = _build_selection_regex()
    m = pat.match(text)
    if not m:
        return  # 不是选择指令, 放行给后续 matcher

    user_id = session.user.id if session.user else "unknown"
    scene_id = session.scene.id if session.scene else "unknown"
    order = music_order.ORDER_STORE.get(user_id, scene_id)
    if order is None:
        # 没有点歌记录, 放行 (par1 等落到 force-parse 流程)
        return

    # 黑名单用户不响应点歌选择(放行给后续 matcher, 不阻断)
    if user_id != "unknown" and auth.is_blacklisted(user_id):
        return

    matcher.stop_propagation()  # 命中点歌选择, 阻断后续 matcher

    idx = int(m.group(1))
    if idx < 1 or idx > len(order.items):
        await UniMessage("序号超出范围,请重新选择").finish()

    item = order.items[idx - 1]

    # 选定后走对应 Parser 已有解析流程, 复用渲染流水线
    try:
        try:
            await UniHelper.message_reaction(event, "resolving")
        except Exception:
            pass

        parser = _get_music_parser(item.platform)
        if item.platform == "netease":
            result = await parser._parse_by_song_id(  # type: ignore[attr-defined]
                item.song_id, share_url=f"https://music.163.com/song/{item.song_id}"
            )
        elif item.platform == "qqmusic":
            result = await parser._parse_by_song_id(  # type: ignore[attr-defined]
                item.song_id, share_url=f"https://y.qq.com/n/ryqq/songDetail/{item.song_id}"
            )
        else:  # kugou
            result = await parser._parse_by_hash(  # type: ignore[attr-defined]
                item.song_id, share_url=f"https://www.kugou.com/song/#hash={item.song_id}"
            )

        await _send_parse_result(result)

        # 选择成功后清理候选 (一次性消费, 避免重复选择同一列表)
        music_order.ORDER_STORE.clear(user_id, scene_id)

        try:
            await UniHelper.message_reaction(event, "done")
        except Exception:
            pass

    except TipException as e:
        try:
            await UniMessage(e.message).send()
        except Exception:
            logger.exception("发送 TipException 提示失败")
        try:
            await UniHelper.message_reaction(event, "done")
        except Exception:
            pass
    except Exception:
        logger.exception("点歌选择解析失败")
        try:
            await UniMessage("解析失败,请稍后重试").send()
        except Exception:
            pass
        try:
            await UniHelper.message_reaction(event, "fail")
        except Exception:
            pass
