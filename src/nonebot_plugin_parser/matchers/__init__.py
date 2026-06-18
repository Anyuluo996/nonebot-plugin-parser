import re
from copy import deepcopy
from typing import TypeVar

from nonebot import logger, get_driver, on_command
from nonebot.params import CommandArg
from nonebot.typing import T_State
from nonebot.matcher import Matcher, current_event
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession

from .rule import SUPER_PRIVATE, PSR_FORCE_PARSE_KEY, Searched, SearchResult, on_keyword_regex
from ..utils import LimitedSizeDict
from .filter import is_tg_authorized, is_platform_enabled
from ..config import gconfig, pconfig
from ..helper import UniHelper, UniMessage
from ..parsers import BaseParser, ParseResult, BilibiliParser
from ..renders import get_renderer
from ..exception import TipException


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


def _get_cached_result(cache_key: str) -> ParseResult | None:
    result = _RESULT_CACHE.get(cache_key)
    if result is None:
        return None
    if not result.is_cache_valid():
        logger.debug(f"缓存失效，移除结果: {cache_key}")
        _RESULT_CACHE.pop(cache_key, None)
        return None
    return deepcopy(result)


def _cache_result(cache_key: str, result: ParseResult) -> None:
    if not result.is_cache_valid():
        logger.debug(f"跳过缓存运行态结果: {cache_key}")
        return
    _RESULT_CACHE[cache_key] = deepcopy(result)


def clear_result_cache():
    _RESULT_CACHE.clear()


async def parser_handler(
    sr: SearchResult = Searched(),
    session: Session = UniSession(),
    state: T_State = None,
):
    """统一的解析处理器"""
    # 1. 获取对应平台 parser
    parser = get_parser(sr.keyword)

    # 2. 检查是否使用前缀强制触发
    force_parse = state.get(PSR_FORCE_PARSE_KEY, False) if state else False
    logger.debug(f"强制解析标记: {force_parse}, state keys: {list(state.keys()) if state else 'None'}")

    # 3. 检查平台是否在当前群组被禁用（强制解析时跳过此检查）
    platform_enabled = is_platform_enabled(session, parser.platform.name)
    logger.debug(f"平台 {parser.platform.name} 启用状态: {platform_enabled}")

    if not force_parse and not platform_enabled:
        logger.debug(f"平台 {parser.platform.name} 在群组 {session.scene_path} 中已被禁用，跳过解析")
        return

    # 3.1 Telegram 解析需额外权限：仅 SUPERUSER 或被授权用户可用
    if parser.platform.name == "telegram":
        user_id = session.user.id if session.user else None
        is_super = user_id is not None and str(user_id) in set(gconfig.superusers)
        authorized = is_super or (user_id is not None and is_tg_authorized(str(user_id)))
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
                logger.warning(f"合并转发发送失败({send_err!r}), 降级为逐条直接发送 {len(nodes)} 条")
                for node_msg in nodes:
                    try:
                        await node_msg.send()
                    except Exception:
                        logger.warning(f"降级发送单条消息失败, 跳过该条: {send_err!r}")

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
    except Exception:
        # 发生错误，添加"失败"表情
        try:
            await UniHelper.message_reaction(event, "fail")
        except Exception:
            pass
        raise


@on_command("bm", priority=3, block=True).handle()
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

    @on_command("ym", priority=3, block=True).handle()
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


@on_command("blogin", block=True, permission=SUPER_PRIVATE).handle()
async def _():
    parser = get_parser_by_type(BilibiliParser)
    qrcode = await parser.login_with_qrcode()
    await UniMessage(UniHelper.img_seg(qrcode)).send()
    async for msg in parser.check_qr_state():
        await UniMessage(msg).send()


# ==================== Telegram 解析授权管理 ====================
# Telegram 解析需消耗本机 tdl 会话，仅 SUPERUSER 可用，
# SUPERUSER 可通过以下命令授权/取消授权其他用户
from .filter import add_tg_whitelist, get_tg_whitelist, remove_tg_whitelist


def _normalize_user_id(arg: str) -> str | None:
    """从命令参数中提取用户 id，支持 `@用户名`(部分适配器) 或纯数字 id。
    返回 None 表示无法识别。
    """
    arg = arg.strip()
    if not arg:
        return None
    # 去掉 @ 前缀
    if arg.startswith("@"):
        arg = arg[1:]
    return arg or None


@on_command("tg授权", block=True, permission=SUPERUSER).handle()
async def _tg_grant(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER: 授权用户使用 Telegram 解析。用法：tg授权 <用户ID/@用户名>"""
    user_id = _normalize_user_id(args.extract_plain_text())
    if not user_id:
        await matcher.finish("用法: tg授权 <用户ID 或 @用户名>")
    if add_tg_whitelist(user_id):
        await matcher.finish(f"已授权 {user_id} 使用 Telegram 解析")
    await matcher.finish(f"{user_id} 已在授权列表中")


@on_command("tg取消授权", block=True, permission=SUPERUSER).handle()
async def _tg_revoke(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER: 取消用户的 Telegram 解析授权。用法：tg取消授权 <用户ID/@用户名>"""
    user_id = _normalize_user_id(args.extract_plain_text())
    if not user_id:
        await matcher.finish("用法: tg取消授权 <用户ID 或 @用户名>")
    if remove_tg_whitelist(user_id):
        await matcher.finish(f"已取消 {user_id} 的 Telegram 解析授权")
    await matcher.finish(f"{user_id} 不在授权列表中")


@on_command("tg白名单", block=True, permission=SUPERUSER).handle()
async def _tg_list(matcher: Matcher):
    """SUPERUSER: 查看当前 Telegram 解析授权列表"""
    whitelist = get_tg_whitelist()
    if not whitelist:
        await matcher.finish("当前 Telegram 授权列表为空")
    await matcher.finish("Telegram 解析授权列表:\n" + "\n".join(whitelist))


@on_command("tg登录", block=True, permission=SUPERUSER).handle()
async def _tg_login(matcher: Matcher):
    """SUPERUSER: 触发 tdl 扫码登录，支持 2FA 两步验证密码。

    流程（通过 matcher.state 阶段标记驱动，pause 后从顶部恢复）:
    1. start_login_qr: pty 启动 tdl，捕获二维码 -> 渲染 PNG 发送 -> 提示扫码
    2. wait_login_complete: 等待扫码（自动检测 2FA）
       - 检测到 2FA: 提示用户发密码 -> pause -> 恢复后 submit_2fa_password -> 继续等待
       - 登录成功/失败: 返回结果
    """
    state = matcher.state
    phase = state.get("_tg_phase", "init")

    # ---------- 阶段：2FA 密码已输入，提交并等待 ----------
    if phase == "2fa":
        from ..download import submit_2fa_password, wait_login_complete

        handle = state.get("_tg_handle")
        if handle is None:
            await matcher.finish("登录会话已失效，请重新执行「tg登录」")
            return
        password = matcher.get_plaintext().strip()
        if not password:
            await matcher.pause(prompt="密码不能为空，请重新发送两步验证密码:")
            return
        try:
            await submit_2fa_password(handle, password)
        except Exception as e:
            logger.exception(f"提交 2FA 密码失败: {e}")
            await matcher.finish(f"提交密码失败: {e}")
            return
        success = await wait_login_complete(handle, timeout=30.0)
        if success:
            await matcher.finish("✅ Telegram 登录成功（2FA 验证通过）")
        else:
            await matcher.finish("❌ 2FA 密码错误或登录失败，请重新执行「tg登录」")
        return

    # ---------- 阶段：首次启动登录 ----------
    from ..utils import render_qr_ascii_to_png
    from ..download import start_login_qr, is_tdl_available, wait_login_complete
    from ..exception import ParseException

    if not is_tdl_available():
        await matcher.finish(
            "tdl 不可用，请先安装 tdl (https://github.com/iyear/tdl)，或配置 parser_tdl_path 指向 tdl 路径"
        )

    await matcher.send("正在生成 Telegram 登录二维码…")

    try:
        handle = await start_login_qr(qr_wait_timeout=30.0)
    except ParseException as e:
        await matcher.finish(f"登录启动失败: {e}")
        return

    if handle.error:
        await matcher.finish(f"登录失败: {handle.error}")
        return

    if not handle.ascii_qr:
        await matcher.finish("未能捕获到二维码，请检查代理/网络后重试")
        return

    try:
        png_bytes = render_qr_ascii_to_png(handle.ascii_qr)
    except Exception as e:
        logger.warning(f"渲染二维码失败: {e}")
        await matcher.finish(f"渲染二维码失败: {e}")
        return

    await UniMessage(UniHelper.img_seg(png_bytes)).send()
    await UniMessage("请用 Telegram App「设置 → 设备 → 扫描二维码」扫描上图（请尽快扫）").send()

    success = await wait_login_complete(handle, timeout=120.0)

    # 检测到 2FA：保存 handle，切换阶段，pause 等用户发密码
    if not success and handle.error == "2FA_REQUIRED":
        state["_tg_phase"] = "2fa"
        state["_tg_handle"] = handle
        await matcher.pause(prompt="⚠ 检测到两步验证(2FA)，请直接发送你的两步验证密码完成登录:")
        return

    if success:
        await matcher.finish("✅ Telegram 登录成功")
    else:
        await matcher.finish("⏱ 扫码超时或未确认，请重新执行「tg登录」")
