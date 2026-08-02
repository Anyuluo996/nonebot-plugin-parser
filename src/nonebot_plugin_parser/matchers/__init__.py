"""matcher 包入口。

本模块只保留**核心解析调度**与不依赖凭据/点歌/Telegram 登录的基础命令:
- ``KEYWORD_PARSER_MAP`` 及其访问器 (``get_parser`` / ``get_parser_by_type``)
- 解析结果缓存 ``_RESULT_CACHE`` 与 ``parser_handler``
- ``bm`` / ``ym`` / ``blogin`` 命令
- ``register_parser_matcher`` (启动时按已启用平台注册 keyword matcher)

凭据管理 / 用户授权与黑名单 / Telegram 登录(2FA) / 点歌 等命令已拆到子模块:
- :mod:`matchers.credential`  网易云/QQ音乐登录登出 + 抖音 ttwid/cookie
- :mod:`matchers.admin`       par授权/拉黑等(并导出共享 helper ``_normalize_user_id``)
- :mod:`matchers.tg_login`    tg授权白名单 + tdl 扫码登录(2FA) + pending 清理
- :mod:`matchers.music`       par点歌 搜索/选择

子模块的命令注册(装饰器 / ``_register_*()``)在 import 时即执行, 故本模块末尾
``from . import credential, admin, tg_login, music`` 触发注册。
``music`` 依赖本模块的 ``_send_parse_result`` / ``get_parser_by_type``, 用函数级
懒导入规避循环; 故 ``from . import music`` 必须在二者定义之后(均位于文件前部, 满足)。
"""

import re
from copy import deepcopy
from typing import TypeVar

from nonebot import logger, get_driver, on_command
from nonebot.params import CommandArg
from nonebot.typing import T_State
from nonebot.matcher import current_event
from nonebot.adapters import Message
from nonebot_plugin_uninfo import Session, UniSession

from . import auth
from .rule import PSR_FORCE_PARSE_KEY, Searched, SearchResult, on_keyword_regex
from ..utils import LimitedSizeDict
from .filter import is_tg_authorized, is_platform_enabled
from ..config import gconfig, pconfig
from ..helper import UniHelper, UniMessage
from ..parsers import BaseParser, ParseResult, BilibiliParser
from ..renders import get_renderer
from ..exception import TipException
from ..failure_store import record_failure


def _get_enabled_parser_classes() -> list[type[BaseParser]]:
    disabled_platforms = set(pconfig.disabled_platforms)
    all_subclass = BaseParser.get_all_subclass()
    return [_cls for _cls in all_subclass if _cls.platform.name not in disabled_platforms]


# 关键词 -> Parser 映射
KEYWORD_PARSER_MAP: dict[str, BaseParser] = {}
T = TypeVar("T", bound=BaseParser)


def get_parser(keyword: str) -> BaseParser:
    return KEYWORD_PARSER_MAP[keyword]


def get_parser_by_type(parser_type: type[T]) -> T:
    for parser in KEYWORD_PARSER_MAP.values():
        if isinstance(parser, parser_type):
            return parser
    raise ValueError(f"未找到类型为 {parser_type} 的 parser 实例")


@get_driver().on_startup
def register_parser_matcher():
    enabled_classes = _get_enabled_parser_classes()

    enabled_platforms = []
    for _cls in enabled_classes:
        parser = _cls()
        enabled_platforms.append(parser.platform.display_name)
        for keyword, _ in _cls._key_patterns:
            KEYWORD_PARSER_MAP[keyword] = parser
    logger.info(f"启用平台: {', '.join(sorted(enabled_platforms))}")

    patterns = [p for _cls in enabled_classes for p in _cls._key_patterns]
    matcher = on_keyword_regex(*patterns)
    matcher.append_handler(parser_handler)


# 缓存结果
_RESULT_CACHE = LimitedSizeDict[str, ParseResult](max_size=50)

# 合并转发发送失败时，降级为逐条直发的上限条数，避免数百节点刷屏
_MAX_FALLBACK_NODES = 20


def _get_cached_result(cache_key: str) -> ParseResult | None:
    result = _RESULT_CACHE.get(cache_key)
    if result is None:
        return None
    if not result.is_cache_valid():
        logger.debug(f"缓存失效，移除结果: {cache_key}")
        _RESULT_CACHE.pop(cache_key, None)
        return None
    # NOTE: deepcopy 在 is_cache_valid 通过后执行, 之间存在 TOCTOU 窗口
    # (并发下另一协程可能修改 result.repost 的延迟任务)。单线程 asyncio 下
    # dict 操作无 await, 实际安全; 若未来在 deepcopy 前插入 await 需重新评估。
    return deepcopy(result)


def _cache_result(cache_key: str, result: ParseResult) -> None:
    if not result.is_cache_valid():
        logger.debug(f"跳过缓存运行态结果: {cache_key}")
        return
    _RESULT_CACHE[cache_key] = deepcopy(result)


def clear_result_cache():
    _RESULT_CACHE.clear()


async def _send_parse_result(result: ParseResult) -> None:
    """渲染并逐条发送 ``ParseResult``（含合并转发失败降级逻辑）。

    抽自 :func:`parser_handler`,供「点歌选择后复用现有渲染流水线」场景调用
    (见 :mod:`matchers.music`), 避免渲染+发送代码重复。
    """
    renderer = get_renderer(result.platform.name)
    async for message in renderer.render_messages(result):
        try:
            await message.send()
        except Exception as send_err:
            # 合并转发发送失败 (如 NTQQ sendMsg 超时) 时, 降级为逐条直接发送,
            # 提升协议端兼容性; 非合并转发消息的重发失败才向上抛出。
            nodes = UniHelper.extract_forward_nodes(message)
            if len(nodes) <= 1:
                # 不是合并转发或仅单节点, 重发无意义, 抛出原异常
                raise
            # 降级直发上限：避免数百节点刷屏（如 opus 长文图文字段落）
            fallback_nodes = nodes[:_MAX_FALLBACK_NODES]
            logger.warning(f"合并转发发送失败({send_err!r}), 降级为逐条直接发送 {len(fallback_nodes)}/{len(nodes)} 条")
            for node_msg in fallback_nodes:
                try:
                    await node_msg.send()
                except Exception:
                    logger.warning(f"降级发送单条消息失败, 跳过该条: {send_err!r}")
            if len(nodes) > _MAX_FALLBACK_NODES:
                try:
                    await UniMessage(f"（合并转发失败，仅显示前 {_MAX_FALLBACK_NODES} 条，共 {len(nodes)} 条）").send()
                except Exception:
                    logger.warning("降级提示发送失败，跳过")


async def parser_handler(
    sr: SearchResult = Searched(),
    session: Session = UniSession(),
    state: T_State = {},
):
    """统一的解析处理器"""
    # 1. 获取对应平台 parser
    parser = get_parser(sr.keyword)

    # 2. 检查是否使用前缀强制触发
    force_parse = state.get(PSR_FORCE_PARSE_KEY, False)
    logger.debug(f"强制解析标记: {force_parse}, state keys: {list(state.keys())}")

    # 3. 检查平台是否在当前群组被禁用（强制解析时跳过此检查）
    platform_enabled = is_platform_enabled(session, parser.platform.name)
    logger.debug(f"平台 {parser.platform.name} 启用状态: {platform_enabled}")

    if not force_parse and not platform_enabled:
        logger.debug(f"平台 {parser.platform.name} 在群组 {session.scene_path} 中已被禁用，跳过解析")
        return

    # 3.05 黑名单: 全局封禁用户不解析(自动解析 + 前缀强制解析都不做)
    _session_user = getattr(session, "user", None)
    user_id = str(_session_user.id) if _session_user else None
    if user_id and auth.is_blacklisted(user_id):
        logger.info(f"用户 {user_id} 在全局黑名单中, 跳过解析")
        return

    # 3.06 前缀强制解析授权: 仅当平台被群管「关闭解析」后, 前缀强制解析才需授权。
    # 平台开着时人人可用前缀(行为零变化); 平台关了, 只有被授权的用户能 par+链接 绕过。
    if force_parse and not platform_enabled:
        if not user_id or not auth.is_force_parse_authorized(user_id, session):
            logger.info(f"用户 {user_id} 无强制解析权限, 平台 {parser.platform.name} 已被关闭")
            raise TipException("该平台已被关闭，你没有强制解析权限，请联系管理员")

    # 3.1 Telegram 解析需额外权限：仅 SUPERUSER 或被授权用户可用
    if parser.platform.name == "telegram":
        is_super = user_id is not None and user_id in set(gconfig.superusers)
        authorized = is_super or (user_id is not None and is_tg_authorized(user_id))
        if not authorized:
            logger.info(f"用户 {user_id} 无 Telegram 解析权限，拒绝")
            raise TipException("无 Telegram 解析权限，请联系 SUPERUSER 执行「tg授权」")

    # 3. 添加"处理中"表情
    event = current_event.get()
    try:
        await UniHelper.message_reaction(event, "resolving")
    except Exception:
        pass  # 如果不支持表情，忽略错误

    try:
        # 4. 获取缓存结果
        cache_key = sr.searched.group(0)
        result = _get_cached_result(cache_key)

        if result is None:
            # 5. 执行解析
            result = await parser.parse(sr.keyword, sr.searched)
            logger.debug(f"解析结果: {result}")
        else:
            logger.debug(f"命中缓存: {cache_key}, 结果: {result}")

        # 6. 渲染内容消息并发送
        await _send_parse_result(result)

        # 7. 缓存解析结果
        _cache_result(cache_key, result)

        # 8. 添加"完成"表情
        try:
            await UniHelper.message_reaction(event, "done")
        except Exception:
            pass

    except TipException as e:
        # 可恢复的用户提示：发消息，不冒泡成 ERROR
        try:
            await UniMessage(e.message).send()
        except Exception:
            logger.exception("发送 TipException 提示失败")
        try:
            await UniHelper.message_reaction(event, "done")
        except Exception:
            pass
    except Exception as e:
        # 记录解析失败到本地（供维护者排查，不影响主流程）
        record_failure(
            url=sr.searched.group(0),
            platform=parser.platform.name,
            error=f"{type(e).__name__}: {e!s}",
        )
        # 发生错误，添加"失败"表情
        try:
            await UniHelper.message_reaction(event, "fail")
        except Exception:
            pass
        raise


@on_command("bm", priority=3, block=True, rule=auth.not_blacklisted()).handle()
@UniHelper.with_reaction
async def _(message: Message = CommandArg()):
    text = message.extract_plain_text()
    matched = re.search(r"(BV[A-Za-z0-9]{10})(\s\d{1,3})?", text)
    if not matched:
        await UniMessage("请发送正确的 BV 号").finish()

    bvid, page_num = matched.group(1), matched.group(2)
    page_idx = int(page_num) if page_num else 0

    parser = get_parser_by_type(BilibiliParser)

    _, audio_url = await parser.extract_download_urls(bvid=bvid, page_index=page_idx)
    if not audio_url:
        await UniMessage("未找到可下载的音频").finish()

    audio_path = await parser.downloader.download_audio(
        audio_url, audio_name=f"{bvid}-{page_idx}.mp3", ext_headers=parser.headers
    )
    await UniMessage(UniHelper.record_seg(audio_path)).send()

    if pconfig.need_upload:
        await UniMessage(UniHelper.file_seg(audio_path)).send()


from ..download import YTDLP_DOWNLOADER

if YTDLP_DOWNLOADER is not None:
    from ..parsers import YouTubeParser

    @on_command("ym", priority=3, block=True, rule=auth.not_blacklisted()).handle()
    @UniHelper.with_reaction
    async def _(message: Message = CommandArg()):
        text = message.extract_plain_text()
        parser = get_parser_by_type(YouTubeParser)
        _, matched = parser.search_url(text)
        if not matched:
            await UniMessage("请发送正确的油管链接").finish()

        url = matched.group(0)

        audio_path = await YTDLP_DOWNLOADER.download_audio(url)
        await UniMessage(UniHelper.record_seg(audio_path)).send()

        if pconfig.need_upload:
            await UniMessage(UniHelper.file_seg(audio_path)).send()


@on_command("blogin", block=True, permission=auth.private_authorized(auth.BILI_LOGIN)).handle()
async def _():
    parser = get_parser_by_type(BilibiliParser)
    qrcode = await parser.login_with_qrcode()
    await UniMessage(UniHelper.img_seg(qrcode)).send()
    async for msg in parser.check_qr_state():
        await UniMessage(msg).send()


# ── 触发子模块命令注册 ──────────────────────────────────────────────
# credential / admin / tg_login 不依赖本模块内部符号, 可任意顺序导入;
# music 通过函数级懒导入取用 _send_parse_result / get_parser_by_type(已在上文定义),
# 故此处 import 安全。每个子模块在自身 import 时执行 _register_*() / 装饰器注册命令。
from . import admin, music, tg_login, credential  # noqa: F401

# ── 向后兼容 re-export ──────────────────────────────────────────────
# 拆分前 _normalize_user_id / _tg_login / _tg_password / _tg_2fa_pending 定义在
# 本模块, 现迁移到 admin / tg_login 子模块。测试与潜在外部代码仍从
# ``nonebot_plugin_parser.matchers`` 顶层导入, 这里重新导出避免破坏。
from .admin import _normalize_user_id  # noqa: F401
from .tg_login import _tg_login, _tg_password, _tg_2fa_pending  # noqa: F401
