import re
from copy import deepcopy
from typing import TYPE_CHECKING, TypeVar

from nonebot import logger, get_driver, on_command, on_message
from nonebot.params import CommandArg
from nonebot.typing import T_State
from nonebot.matcher import Matcher, current_event
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER
from nonebot_plugin_uninfo import Session, UniSession

if TYPE_CHECKING:
    from ..download import LoginQrHandle

from .rule import SUPER_PRIVATE, PSR_FORCE_PARSE_KEY, Searched, SearchResult, on_keyword_regex
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


async def _send_parse_result(result: ParseResult) -> None:
    """渲染并逐条发送 ``ParseResult``（含合并转发失败降级逻辑）。

    抽自 :func:`parser_handler`,供「点歌选择后复用现有渲染流水线」场景调用,
    避免渲染+发送代码重复。
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
            logger.warning(f"合并转发发送失败({send_err!r}), 降级为逐条直接发送 {len(nodes)} 条")
            for node_msg in nodes:
                try:
                    await node_msg.send()
                except Exception:
                    logger.warning(f"降级发送单条消息失败, 跳过该条: {send_err!r}")


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
            error=f"{type(e).__name__}: {getattr(e, 'message', e)}",
        )
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


# ==================== 音乐平台扫码登录 ====================
# 命令名 = <prefix>+平台登录/登出, 启动时按配置的 prefix 动态生成。
# 网易云/QQ 音乐登录后 VIP 歌曲可解析；酷狗登录功能开发中。
# VIP 过滤与登录态联动：已登录的平台搜索结果不再过滤付费曲（见 music_search）。
async def _netease_login(matcher: Matcher, parser: BaseParser):
    """网易云扫码登录（公开 GET 接口，无需第三方库/加密）。

    三步：unikey → 二维码图片(用 codekey 构造 URL) → 轮询 client/login 拿 cookie。
    """
    import io
    import asyncio

    import qrcode

    from ..parsers.netease import credential as netease_cred

    await matcher.send("正在生成网易云登录二维码…")
    logger.info("网易云登录: 开始生成二维码")
    try:
        # 1. 获取 unikey（必须带 type=1，否则返回 400 参数错误）
        resp = await parser.request(
            "https://music.163.com/api/login/qrcode/unikey",
            method="POST",
            params={"type": 1},
            headers={"Referer": "https://music.163.com/"},
            raise_for_status=False,
        )
        unikey = resp.json().get("unikey")
        if not unikey:
            logger.warning(f"网易云登录: 未拿到 unikey (status={resp.status_code})")
            await matcher.finish("获取登录 key 失败，请稍后重试")
            return
        logger.info(f"网易云登录: 获取 unikey 成功 (len={len(unikey)})")

        # 2. 用 codekey 构造登录 URL 并生成二维码图片
        login_url = f"https://music.163.com/login?codekey={unikey}"
        buf = io.BytesIO()
        qrcode.make(login_url).save(buf)
        await UniMessage(UniHelper.img_seg(buf.getvalue())).send()
        await UniMessage("请用网易云音乐 App 扫描上图登录（请尽快扫，二维码 180 秒内有效）").send()

        # 3. 轮询登录状态（1.5s 间隔，180s 超时）
        # 已知码：800=过期, 801=等待扫码, 802=已扫码待确认, 803=成功
        # 未知码（如 8821 风控）：首次 warning 容错，连续 2 次判定为被拒并终止
        unknown_code: int | None = None
        unknown_count = 0
        scanned_notified = False  # 802 只提示一次「请在手机确认」
        for _ in range(120):
            await asyncio.sleep(1.5)
            resp = await parser.request(
                "https://music.163.com/api/login/qrcode/client/login",
                params={"key": unikey, "type": 1},
                headers={"Referer": "https://music.163.com/"},
                raise_for_status=False,
            )
            data = resp.json()
            code = data.get("code")
            # 高频轮询状态打到 debug，避免淹没日志
            logger.debug(f"网易云登录轮询: code={code}")
            if code == 803:
                # 登录成功：cookie 优先取 Set-Cookie 响应头（官方接口行为），
                # 兜底取响应体 cookie 字段（第三方库封装格式）
                cookie_str = "; ".join(f"{k}={v}" for k, v in resp.cookies.items())
                if "MUSIC_U" not in cookie_str:
                    cookie_str = data.get("cookie", "")
                if "MUSIC_U" not in cookie_str:
                    logger.warning(
                        "网易云登录: 803 响应未含 MUSIC_U, "
                        f"Set-Cookie keys={list(resp.cookies.keys())}, "
                        f"body cookie={'有' if data.get('cookie') else '无'}"
                    )
                    await matcher.finish("登录响应异常（未拿到 MUSIC_U），请重试")
                    return
                netease_cred.save_credential(cookie_str)
                logger.info("网易云登录: 扫码成功，已保存凭证")
                await matcher.finish("✅ 网易云登录成功，VIP 歌曲现可解析")
                return
            if code == 800:
                logger.info("网易云登录: 二维码已过期")
                await matcher.finish("二维码已过期，请重新执行登录指令")
                return
            if code == 801:
                # 等待扫码，继续轮询
                continue
            if code == 802:
                # 已扫码，等待手机确认（只提示一次，避免刷屏）
                if not scanned_notified:
                    logger.info("网易云登录: 已扫码，等待手机确认")
                    await matcher.send("📱 已检测到扫码，请在手机上点击「确认登录」")
                    scanned_notified = True
                continue
            # 未知码（8821 等）：首次记录并容错一次，连续出现则判定被拒/风控
            if code != unknown_code:
                unknown_code = code
                unknown_count = 1
                logger.warning(f"网易云登录: 出现未知轮询码 code={code}, message={data.get('message')!r}")
                continue
            unknown_count += 1
            if unknown_count >= 2:
                logger.warning(f"网易云登录: 未知码 code={code} 连续 {unknown_count} 次，判定为登录被拒/风控拦截")
                await matcher.finish(
                    f"⚠️ 登录被服务端拒绝（code={code}），可能触发风控。\n"
                    "请稍后重试，或更换网络环境（如切换到手机热点）后再试。"
                )
                return
        logger.info("网易云登录: 180s 内未完成扫码")
        await matcher.finish("登录超时（180s 内未完成扫码），请重试")
    except Exception as e:
        logger.exception("网易云登录异常")
        await matcher.finish(f"登录未完成: {e}")


async def _qqmusic_login(matcher: Matcher):
    """QQ 音乐扫码登录（依赖 qqmusic-api-python）。"""
    from ..parsers import _QQMUSIC_AVAILABLE

    if not _QQMUSIC_AVAILABLE:
        await matcher.finish("qqmusic-api-python 未安装，该指令不可用。请先 `pip install qqmusic-api-python`")
        return
    from qqmusic_api import Client
    from qqmusic_api.models.login import QRLoginType
    from qqmusic_api.modules.login_utils import QRCodeLoginSession

    from ..parsers.qqmusic import credential as qq_cred

    await matcher.send("正在生成 QQ 音乐登录二维码…")
    logger.info("QQ音乐登录: 开始生成二维码")
    try:
        async with Client() as client:
            session = QRCodeLoginSession(
                client.login,
                QRLoginType.QQ,
                interval=1.5,
                timeout_seconds=180.0,
            )
            qr = await session.get_qrcode()
            await UniMessage(UniHelper.img_seg(qr.data)).send()
            await UniMessage("请用手机 QQ 扫描上图授权登录（请尽快扫，二维码 180 秒内有效）").send()
            cred = await session.wait_qrcode_login()
            qq_cred.save_credential(cred)
    except Exception as e:
        logger.exception("QQ音乐登录异常")
        await matcher.finish(f"登录未完成: {e}")
        return
    logger.info(f"QQ音乐登录: 成功 (musicid={cred.musicid})")
    await matcher.finish(f"✅ QQ 音乐登录成功 (musicid={cred.musicid})，VIP 歌曲现可解析")


def _register_music_login_commands() -> None:
    """动态注册音乐平台登录/登出命令（命令名 = <prefix>+别名）。

    与点歌命令（``par点歌``/``par网易云``）同源，复用 ``parse_prefix``。
    用闭包工厂生成 handler，避免循环变量陷阱并保持 nonebot 依赖注入签名。
    """
    prefix = pconfig.parse_prefix or "par"

    def _make_netease_login():
        async def _h(matcher: Matcher):
            await _netease_login(matcher, get_parser_by_type(NCMParser))

        return _h

    def _make_netease_logout():
        async def _h(matcher: Matcher):
            await _netease_logout(matcher)

        return _h

    # 网易云：网易云/wyy 两个别名（与点歌别名一致）
    for _alias in ("网易云", "wyy"):
        on_command(f"{prefix}{_alias}登录", block=True, permission=SUPERUSER).handle()(_make_netease_login())
        on_command(f"{prefix}{_alias}登出", block=True, permission=SUPERUSER).handle()(_make_netease_logout())

    # QQ 音乐：仅 qq 别名（与点歌别名一致）
    on_command(f"{prefix}qq登录", block=True, permission=SUPERUSER).handle()(_qqmusic_login)
    on_command(f"{prefix}qq登出", block=True, permission=SUPERUSER).handle()(_qqmusic_logout)


async def _netease_logout(matcher: Matcher):
    from ..parsers.netease import credential as netease_cred

    if netease_cred.clear_credential():
        logger.info("网易云: 已清除登录态")
        await matcher.finish("已清除网易云登录态，后续仅能解析免费歌曲")
    await matcher.finish("当前未保存网易云登录态")


async def _qqmusic_logout(matcher: Matcher):
    """SUPERUSER: 清除已保存的 QQ 音乐登录态。"""
    from ..parsers import _QQMUSIC_AVAILABLE

    if not _QQMUSIC_AVAILABLE:
        await matcher.finish("qqmusic-api-python 未安装，该指令不可用。请先 `pip install qqmusic-api-python`")
        return
    from ..parsers.qqmusic import credential as qq_cred

    if qq_cred.clear_credential():
        logger.info("QQ音乐: 已清除登录态")
        await matcher.finish("已清除 QQ 音乐登录态，后续仅能解析免费歌曲")
    await matcher.finish("当前未保存 QQ 音乐登录态")


_register_music_login_commands()


# ==================== 抖音 ttwid 凭据管理 ====================
# 抖音图文/实况照片需登录态 ttwid + a_bogus 签名配套才放行。
# 除 .env 兜底外，SUPERUSER 可通过指令热更新 ttwid，无需重启。
@on_command("dyttwid", block=True, permission=SUPERUSER).handle()
async def _douyin_set_ttwid(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER: 写入抖音登录态 ttwid（持久化，热更新，覆盖上一次的值）。

    用法: dyttwid <ttwid 值>   —— 从浏览器登录抖音后复制 ``ttwid`` Cookie。
    优先级高于 .env 的 ``parser_douyin_ttwid``，写入后立即生效。
    """
    from ..parsers.douyin import ttwid as dy_ttwid

    value = args.extract_plain_text().strip()
    if not value:
        await matcher.finish("用法: dyttwid <ttwid 值>（从浏览器登录抖音后复制 ttwid Cookie）")
    dy_ttwid.save_ttwid(value)
    await matcher.finish(f"✅ 已保存抖音 ttwid（{len(value)} 字符），图文/实况照片解析立即生效")


@on_command("dyttwid查看", block=True, permission=SUPERUSER).handle()
async def _douyin_show_ttwid(matcher: Matcher):
    """SUPERUSER: 查看当前生效的抖音 ttwid（排查设置是否成功）。"""
    from ..parsers.douyin import ttwid as dy_ttwid

    persisted = dy_ttwid.load_ttwid()
    effective = dy_ttwid.get_effective_ttwid()
    if persisted is not None:
        # 指令持久化生效，显示实际值以便核对
        await matcher.finish(f"当前生效抖音 ttwid（来源: 指令持久化）:\n{effective}")
    if effective is not None:
        # 无持久化，回落到 .env
        await matcher.finish(f"当前生效抖音 ttwid（来源: .env parser_douyin_ttwid）:\n{effective}")
    await matcher.finish("当前未配置抖音 ttwid（指令和 .env 均为空）")


@on_command("dycookie", block=True, permission=SUPERUSER).handle()
async def _douyin_set_cookie(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER: 写入抖音完整登录态 Cookie（持久化，热更新，覆盖上一次的值）。

    用法: dycookie <整条 Cookie> —— 从浏览器 F12 → Network → www.douyin.com →
    Request Headers → Cookie 整行复制（含 sessionid/sid_guard/odin_tt/ttwid 等）。
    优先级高于 dyttwid / parser_douyin_ttwid，写入后立即生效。
    完整 Cookie 比仅 ttwid 抗风控能力更强（含完整登录态字段）。
    """
    from ..parsers.douyin import ttwid as dy_ttwid

    value = args.extract_plain_text().strip()
    if not value:
        await matcher.finish(
            "用法: dycookie <整条 Cookie>\n（从浏览器 F12 → Network → www.douyin.com → Cookie 整行复制）"
        )
    dy_ttwid.save_cookie(value)
    await matcher.finish(f"✅ 已保存抖音 Cookie（{len(value)} 字符），图文/实况照片解析立即生效")


@on_command("dycookie查看", block=True, permission=SUPERUSER).handle()
async def _douyin_show_cookie(matcher: Matcher):
    """SUPERUSER: 查看当前生效的抖音凭据（排查设置是否成功）。"""
    from ..parsers.douyin import ttwid as dy_ttwid

    # 优先显示完整 cookie（若已配置）
    cookie_persisted = dy_ttwid.load_cookie()
    cookie_effective = dy_ttwid.get_effective_cookie()
    if cookie_persisted is not None:
        # 脱敏：只显示前 40 字符，完整 cookie 太长且含敏感登录态
        preview = cookie_effective[:40] + "..." if cookie_effective and len(cookie_effective) > 40 else cookie_effective
        await matcher.finish(f"当前生效抖音 Cookie（来源: 指令持久化）:\n{preview}")
    if cookie_effective is not None:
        preview = cookie_effective[:40] + "..." if len(cookie_effective) > 40 else cookie_effective
        await matcher.finish(f"当前生效抖音 Cookie（来源: .env parser_douyin_cookie）:\n{preview}")

    # 无 cookie，回退显示 ttwid 状态
    ttwid_effective = dy_ttwid.get_effective_ttwid()
    if ttwid_effective is not None:
        ttwid_persisted = dy_ttwid.load_ttwid()
        source = "指令持久化" if ttwid_persisted is not None else ".env parser_douyin_ttwid"
        await matcher.finish(f"未配置完整 Cookie，当前回退使用 ttwid（来源: {source}）:\n{ttwid_effective}")
    await matcher.finish("当前未配置抖音凭据（cookie/ttwid 指令和 .env 均为空）")


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


# 模块级 2FA 密码等待状态：{user_id: LoginQrHandle}
# tg登录 检测到 2FA 时写入，tg密码 matcher 消费后删除
# LoginQrHandle 仅用于类型标注（TYPE_CHECKING 避免运行时循环导入）
_tg_2fa_pending: dict[str, "LoginQrHandle"] = {}


@on_command("tg登录", block=True, permission=SUPERUSER).handle()
async def _tg_login(matcher: Matcher):
    """SUPERUSER: 触发 tdl 扫码登录，支持 2FA 两步验证密码。

    流程:
    1. start_login_qr: pty 启动 tdl，捕获二维码 -> 渲染 PNG 发送 -> 提示扫码
    2. wait_login_complete: 等待扫码（自动检测 2FA）
       - 登录成功/失败: 直接返回结果
       - 检测到 2FA: 把 handle 存入 _tg_2fa_pending[user_id]，提示用户发密码
         （密码由独立的 _tg_password matcher 接收并提交，不用 pause，
          因为 on_command 的 pause 恢复要求消息仍满足命令格式）
    """
    from ..utils import render_qr_ascii_to_png
    from ..download import start_login_qr, is_tdl_available, wait_login_complete
    from ..exception import ParseException

    if not is_tdl_available():
        await matcher.finish(
            "tdl 不可用，请先安装 tdl (https://github.com/iyear/tdl)，或配置 parser_tdl_path 指向 tdl 路径"
        )

    user_id = str(event.user_id) if (event := current_event.get()) else "unknown"  # type: ignore[attr-defined]
    # 清理该用户之前的 pending（避免残留）
    _tg_2fa_pending.pop(user_id, None)

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
    logger.debug(f"wait_login_complete 返回 success={success} error={handle.error!r}")

    # 检测到 2FA：保存 handle 到 pending，由独立密码 matcher 接收密码
    if not success and handle.error == "2FA_REQUIRED":
        logger.info(f"检测到 2FA，用户 {user_id} 进入密码输入等待")
        _tg_2fa_pending[user_id] = handle
        await matcher.finish("⚠ 检测到两步验证(2FA)，请在 30 秒内直接发送你的两步验证密码完成登录（仅发密码）")
        return

    if success:
        await matcher.finish("✅ Telegram 登录成功")
    else:
        logger.debug(f"登录未成功（非2FA），error={handle.error!r}")
        await matcher.finish("⏱ 扫码超时或未确认，请重新执行「tg登录」")


@on_message(block=False).handle()
async def _tg_password(matcher: Matcher):
    """接收 2FA 密码：仅当用户在 _tg_2fa_pending 中时，把消息作为密码提交给 tdl。

    不用 on_command/pause，因为恢复时纯密码不以命令前缀开头会被拒绝。
    用 on_message + 显式 pending 检查，实现条件性多轮交互。
    """
    if not _tg_2fa_pending:
        return  # 没人在等 2FA 密码，跳过

    event_obj = current_event.get()
    if event_obj is None or not hasattr(event_obj, "user_id"):
        return
    user_id = str(event_obj.user_id)  # type: ignore[attr-defined]
    handle = _tg_2fa_pending.get(user_id)
    if handle is None:
        return  # 该用户没在等密码，跳过

    from ..download import submit_2fa_password, wait_login_complete

    # 从 event 取纯文本（get_plaintext 是 MessageEvent 的方法，非 Matcher）
    password = event_obj.get_plaintext().strip() if hasattr(event_obj, "get_plaintext") else ""
    logger.debug(f"收到 {user_id} 的 2FA 密码，长度={len(password)}")
    if not password:
        # 空消息，忽略（等真正的密码）
        return

    # 消费 pending（一次性）
    _tg_2fa_pending.pop(user_id, None)
    matcher.stop_propagation()  # 阻断其他 matcher 处理这条密码消息

    try:
        await submit_2fa_password(handle, password)
        logger.info(f"已提交 {user_id} 的 2FA 密码，等待 tdl 验证")
    except Exception as e:
        logger.exception(f"提交 2FA 密码失败: {e}")
        await matcher.send(f"提交密码失败: {e}")
        return

    await matcher.send("正在验证 2FA 密码…")
    success = await wait_login_complete(handle, timeout=30.0)
    logger.debug(f"2FA 提交后 wait_login 结果: {success}")
    if success:
        await matcher.finish("✅ Telegram 登录成功（2FA 验证通过）")
    else:
        await matcher.finish("❌ 2FA 密码错误或登录失败，请重新执行「tg登录」")


# ==================== 点歌功能 ====================
# 触发: <prefix>点歌 <歌名> (三服务并发)
#       <prefix>网易云/<prefix>wyy <prefix>qq <prefix>酷狗/<prefix>kg <歌名> (指定服务)
#       <prefix><序号> (选择, 窗口期 5 分钟 + 用户隔离)
# <prefix> = parser_force_prefix, 未配置默认 "par"
from .. import music_order, music_render, music_search
from ..parsers import NCMParser, KuGouParser

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

    # 默认三服务点歌
    @on_command(f"{prefix}点歌", priority=2, block=True).handle()
    async def _order_default(matcher: Matcher, args: Message = CommandArg(), session: Session = UniSession()):
        await _do_music_search(matcher, session, args.extract_plain_text(), _DEFAULT_ORDER_PLATFORMS)

    # 指定服务: 每个别名一个命令
    for alias, platform in _SERVICE_ALIASES.items():
        # 闭包捕获当前 platform (避免循环变量陷阱)
        def _make_handler(plat: music_search.PlatformName):
            async def _handler(matcher: Matcher, args: Message = CommandArg(), session: Session = UniSession()):
                await _do_music_search(matcher, session, args.extract_plain_text(), [plat])

            return _handler

        on_command(f"{prefix}{alias}", priority=2, block=True).handle()(_make_handler(platform))


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
