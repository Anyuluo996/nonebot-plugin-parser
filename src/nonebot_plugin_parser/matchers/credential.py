"""凭据管理命令：网易云/QQ 音乐登录登出 + 抖音 ttwid/cookie。

从 ``matchers/__init__.py`` 拆出。命令名跟随 ``parser_force_prefix``
（未配置回退 ``par``），登录态持久化到本地（见各 ``parsers/<platform>/credential.py``）。

VIP 过滤与登录态联动：已登录的平台搜索结果不再过滤付费曲（见 music_search）。
"""

from nonebot import logger, on_command
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.adapters import Message

from . import auth
from ..config import pconfig
from ..helper import UniHelper, UniMessage

# ==================== 网易云 / QQ 音乐 登录/登出 handler ====================
# 函数定义全部前置到 _register_music_login_commands 之前（原 __init__.py 中
# _netease_logout/_qqmusic_logout 定义在注册之后, 仅靠 Python 函数体延迟解析侥幸
# 工作, 拆分时一并修正为定义先于注册）。


async def _netease_login(matcher: Matcher, args: Message):
    """网易云手动 cookie 导入登录。

    扫码登录（codekey 流程）已被网易云 8821 风控封禁（message="请切换其他
    登录方式"，redirectUrl=anquanhuanjingfengxian 安全环境风险），无论换 IP/UA
    均拦在「确认登录」环节。改为手动 cookie 导入，与抖音 ``dycookie`` 同模式。

    用法: par网易云登录 <整条 Cookie>
      浏览器登录 music.163.com → F12 → Network → 任一请求 → Request Headers →
      Cookie 整行复制（必须含 MUSIC_U）。
    """
    from ..parsers.netease import credential as netease_cred

    value = args.extract_plain_text().strip()
    if not value:
        await matcher.finish(
            "用法: par网易云登录 <整条 Cookie>\n"
            "浏览器登录 music.163.com → F12 → Network → 任一请求 → Cookie 整行复制\n"
            "（必须含 MUSIC_U，不含则视为无效）"
        )
        return

    if "MUSIC_U" not in value:
        await matcher.finish("❌ Cookie 中未找到 MUSIC_U，请确认从已登录的网易云页面复制完整 Cookie")
        return

    netease_cred.save_credential(value)
    logger.info(f"网易云: 已保存手动导入 cookie（{len(value)} 字符）")
    await matcher.finish(f"✅ 已保存网易云 Cookie（{len(value)} 字符），VIP 歌曲现可解析")


async def _netease_logout(matcher: Matcher):
    from ..parsers.netease import credential as netease_cred

    if netease_cred.clear_credential():
        logger.info("网易云: 已清除登录态")
        await matcher.finish("已清除网易云登录态，后续仅能解析免费歌曲")
    await matcher.finish("当前未保存网易云登录态")


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


def _register_music_login_commands() -> None:
    """动态注册音乐平台登录/登出命令（命令名 = <prefix>+别名）。

    与点歌命令（``par点歌``/``par网易云``）同源，复用 ``parse_prefix``。
    用闭包工厂生成 handler，避免循环变量陷阱并保持 nonebot 依赖注入签名。
    """
    prefix = pconfig.parse_prefix or "par"

    def _make_netease_login():
        async def _h(matcher: Matcher, args: Message = CommandArg()):
            await _netease_login(matcher, args)

        return _h

    def _make_netease_logout():
        async def _h(matcher: Matcher):
            await _netease_logout(matcher)

        return _h

    # 网易云：网易云/wyy 两个别名（与点歌别名一致）
    _netease_perm = auth.super_or_authorized(auth.NETEASE_LOGIN)
    for _alias in ("网易云", "wyy"):
        on_command(f"{prefix}{_alias}登录", block=True, permission=_netease_perm).handle()(_make_netease_login())
        on_command(f"{prefix}{_alias}登出", block=True, permission=_netease_perm).handle()(_make_netease_logout())
        auth.register_command_item(f"{prefix}{_alias}登录", auth.NETEASE_LOGIN)
        auth.register_command_item(f"{prefix}{_alias}登出", auth.NETEASE_LOGIN)

    # QQ 音乐：仅 qq 别名（与点歌别名一致）
    _qq_perm = auth.super_or_authorized(auth.QQ_LOGIN)
    on_command(f"{prefix}qq登录", block=True, permission=_qq_perm).handle()(_qqmusic_login)
    on_command(f"{prefix}qq登出", block=True, permission=_qq_perm).handle()(_qqmusic_logout)
    auth.register_command_item(f"{prefix}qq登录", auth.QQ_LOGIN)
    auth.register_command_item(f"{prefix}qq登出", auth.QQ_LOGIN)


# 模块加载时即注册（与原 __init__.py 行为一致）
_register_music_login_commands()


# ==================== 抖音 ttwid 凭据管理 ====================
# 抖音图文/实况照片需登录态 ttwid + a_bogus 签名配套才放行。
# 除 .env 兜底外，SUPERUSER 可通过指令热更新 ttwid，无需重启。
@on_command("dyttwid", block=True, permission=auth.super_or_authorized(auth.DY_TTWID)).handle()
async def _douyin_set_ttwid(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER 或被授权用户: 写入抖音登录态 ttwid（持久化，热更新，覆盖上一次的值）。

    用法: dyttwid <ttwid 值>   —— 从浏览器登录抖音后复制 ``ttwid`` Cookie。
    优先级高于 .env 的 ``parser_douyin_ttwid``，写入后立即生效。
    """
    from ..parsers.douyin import ttwid as dy_ttwid

    value = args.extract_plain_text().strip()
    if not value:
        await matcher.finish("用法: dyttwid <ttwid 值>（从浏览器登录抖音后复制 ttwid Cookie）")
    dy_ttwid.save_ttwid(value)
    await matcher.finish(f"✅ 已保存抖音 ttwid（{len(value)} 字符），图文/实况照片解析立即生效")


@on_command("dyttwid查看", block=True, permission=auth.super_or_authorized(auth.DY_TTWID)).handle()
async def _douyin_show_ttwid(matcher: Matcher):
    """SUPERUSER 或被授权用户: 查看当前生效的抖音 ttwid（排查设置是否成功）。"""
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


@on_command("dycookie", block=True, permission=auth.super_or_authorized(auth.DY_COOKIE)).handle()
async def _douyin_set_cookie(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER 或被授权用户: 写入抖音完整登录态 Cookie（持久化，热更新，覆盖上一次的值）。

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


@on_command("dycookie查看", block=True, permission=auth.super_or_authorized(auth.DY_COOKIE)).handle()
async def _douyin_show_cookie(matcher: Matcher):
    """SUPERUSER 或被授权用户: 查看当前生效的抖音凭据（排查设置是否成功）。"""
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
