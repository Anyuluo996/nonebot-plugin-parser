"""Telegram 解析授权白名单 + tdl 扫码登录(含 2FA 两步验证)。

从 ``matchers/__init__.py`` 拆出。

- Telegram 解析需消耗本机 tdl 会话，仅 SUPERUSER 可用；
  SUPERUSER 可通过 ``tg授权``/``tg取消授权``/``tg白名单`` 授权其他用户。
- ``tg登录`` 触发 tdl 扫码，账号开启两步验证(2FA)时把 ``LoginQrHandle`` 存入
  ``_tg_2fa_pending``，由独立的 ``_tg_password`` message matcher 接收密码。

为防止「用户触发 2FA 后不发密码」导致 pty fd/子进程/读线程泄漏，
``_tg_2fa_pending`` 每项自带 ``expires_at``（窗口 = :data:`_TG_2FA_TTL`，提示文案
与此同源），由包根 ``__init__.py`` 的定时 job ``parser-tg-2fa-cleanup`` 周期性
调用 ``_cleanup_stale_2fa`` 兜底清理明显泄漏的项。
"""

import time
from typing import TYPE_CHECKING
from dataclasses import dataclass

from nonebot import logger, on_command, on_message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher, current_event
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER

from .admin import _extract_target_user
from .filter import add_tg_whitelist, get_tg_whitelist, remove_tg_whitelist
from ..config import pconfig
from ..helper import UniHelper, UniMessage

if TYPE_CHECKING:
    from ..download import LoginQrHandle


#: 2FA 密码等待窗口（秒）。提示文案与 pending 过期判定共用此值。
#: 历史事故: 提示写「30 秒内」而 pending 实际留 600 秒，用户以为早已超时放弃，
#: 期间他发的任意消息仍被当作密码吞掉并 stop_propagation，正常解析全被吃掉。
_TG_2FA_TTL = 120.0

#: 定时 job 的兜底清理阈值（秒，TTL 的 5 倍）。
#: 正常过期由 ``_tg_password`` 自行判定 ``expires_at``；这里只兜「用户放弃 /
#: 掉线 / 进程异常」留下、已无人会消费的 handle（pty fd + tdl 子进程 + 读线程）。
_TG_2FA_CLEANUP_MAX_AGE = _TG_2FA_TTL * 5


@dataclass(slots=True)
class _Pending2FA:
    """一条等待用户输入密码的 2FA 会话。"""

    handle: "LoginQrHandle"
    created_at: float
    """monotonic 秒，进入等待的时刻（``_cleanup_stale_2fa`` 的兜底清理依据）。"""
    expires_at: float
    """monotonic 秒，= ``created_at + _TG_2FA_TTL``；超过即不再接受密码。"""

    @classmethod
    def create(cls, handle: "LoginQrHandle") -> "_Pending2FA":
        now = time.monotonic()
        return cls(handle=handle, created_at=now, expires_at=now + _TG_2FA_TTL)


# 模块级 2FA 密码等待状态：{user_id: _Pending2FA}
# tg登录 检测到 2FA 时写入，tg密码 matcher 消费后删除；
# 超时未消费的项由 _cleanup_stale_2fa 清理（防 pty fd/子进程/读线程泄漏）。
_tg_2fa_pending: dict[str, _Pending2FA] = {}


def _extract_tg_user(message: Message) -> tuple[str | None, str]:
    """从 ``tg授权``/``tg取消授权`` 参数里取被操作用户 id。

    返回 ``(数字用户 id 或 None, 从消息里取到的原始标识文本)``。

    优先 at 段（等价复用 ``admin._extract_target_user`` 的 at 解析）：OneBot 的
    at 段不进 ``extract_plain_text()``，旧实现直接读纯文本，于是 ``tg授权 @某人``
    拿到空串白报用法，手打 ``@alice`` 又会被原样写进白名单（判定时按事件里的
    数字 user_id 比对，永不命中）却还回复「已授权」。白名单既然按数字 id 匹配，
    回退的纯文本就必须是纯数字，其余形态一律判为无效。
    """
    user_id, _rest = _extract_target_user(message)
    if not user_id:
        return None, ""
    user_id = user_id.strip()
    if not user_id:
        return None, ""
    # OneBot 的 @全体成员 会给出 "all"，非数字一律拒
    return (user_id if user_id.isdigit() else None), user_id


# ==================== Telegram 解析授权白名单 ====================
@on_command("tg授权", block=True, permission=SUPERUSER).handle()
async def _tg_grant(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER: 授权用户使用 Telegram 解析。用法：tg授权 <@用户 或 数字用户ID>"""
    user_id, raw = _extract_tg_user(args)
    if not user_id:
        if raw:
            await matcher.finish(
                f"无法识别用户「{raw}」: 白名单按数字用户 ID 匹配, 手打的 @用户名 不会生效"
                " —— 请 @ 目标用户, 或直接发送其数字 ID"
            )
        await matcher.finish("用法: tg授权 <@用户 或 数字用户ID>")
    if add_tg_whitelist(user_id):
        await matcher.finish(f"已授权 {user_id} 使用 Telegram 解析")
    await matcher.finish(f"{user_id} 已在授权列表中")


@on_command("tg取消授权", block=True, permission=SUPERUSER).handle()
async def _tg_revoke(matcher: Matcher, args: Message = CommandArg()):
    """SUPERUSER: 取消用户的 Telegram 解析授权。用法：tg取消授权 <@用户 或 数字用户ID>"""
    user_id, raw = _extract_tg_user(args)
    if not user_id:
        if raw:
            await matcher.finish(
                f"无法识别用户「{raw}」: 白名单按数字用户 ID 匹配, 手打的 @用户名 不会生效"
                " —— 请 @ 目标用户, 或直接发送其数字 ID"
            )
        await matcher.finish("用法: tg取消授权 <@用户 或 数字用户ID>")
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


# ==================== tdl 扫码登录 + 2FA ====================
@on_command("tg登录", block=True, permission=SUPERUSER).handle()
async def _tg_login(matcher: Matcher):
    """SUPERUSER: 触发 tdl 扫码登录，支持 2FA 两步验证密码。

    流程:
    1. start_login_qr: pty 启动 tdl，捕获二维码 -> 渲染 PNG 发送 -> 提示扫码
    2. wait_login_complete: 等待扫码（自动检测 2FA）
       - 登录成功/失败: 直接返回结果
       - 检测到 2FA: 把 handle 包成 _Pending2FA(带过期时刻) 存入
         _tg_2fa_pending[user_id]，提示用户发密码
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
    _cleanup_user_pending(user_id)

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
        _tg_2fa_pending[user_id] = _Pending2FA.create(handle)
        await matcher.finish(
            f"⚠ 检测到两步验证(2FA)，请在 {int(_TG_2FA_TTL)} 秒内直接发送你的两步验证密码完成登录（仅发密码本身）"
        )
        return

    if success:
        await matcher.finish("✅ Telegram 登录成功")
    else:
        logger.debug(f"登录未成功（非2FA），error={handle.error!r}")
        await matcher.finish("⏱ 扫码超时或未确认，请重新执行「tg登录」")


def _looks_like_2fa_password(text: str) -> bool:
    """粗判一条消息是否"像" 2FA 密码，而不是普通聊天或另一条指令。

    历史事故: pending 窗口内该用户的**任意**消息都会被当密码消费并
    ``stop_propagation``（聊天、点歌、其他指令全被吞掉），登录还因为密码错误
    照样失败。这里只做保守的形态判断:

    - 长度 1..64 且不含空白（密码是单个 token，正常聊天几乎总带空格/多词）
    - 不是 URL（含 ``://`` 或以 ``www.`` 开头）
    - 不以本插件命令前缀开头（``par点歌 xx`` 这类不能被吃）
    - 至少含一个 ASCII 字母/数字
    - 不含中日韩字符（群聊里纯中文消息占绝大多数；极少数含中文的密码会被
      判为非密码 —— 用户看到登录没成功可重新触发，比吞掉正常消息代价小）

    判为"不像"时调用方不消费、不阻断，消息继续走正常解析。
    """
    if not text or len(text) > 64:
        return False
    if any(ch.isspace() for ch in text):
        return False
    if "://" in text or text.startswith("www."):
        return False
    if text.startswith(pconfig.parse_prefix or "par"):
        return False
    if not any(ch.isascii() and ch.isalnum() for ch in text):
        return False
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af":
            return False
    return True


@on_message(block=False).handle()
async def _tg_password(matcher: Matcher):
    """接收 2FA 密码：仅当用户在 _tg_2fa_pending 中且未过期时提交给 tdl。

    不用 on_command/pause，因为恢复时纯密码不以命令前缀开头会被拒绝。
    用 on_message + 显式 pending 检查，实现条件性多轮交互。

    过期判定必须先于消费：窗口过期后该用户的消息要回归正常解析（不
    ``stop_propagation``），同时把残留 handle 终止掉防 pty fd/子进程泄漏。
    """
    if not _tg_2fa_pending:
        return  # 没人在等 2FA 密码，跳过

    event_obj = current_event.get()
    if event_obj is None or not hasattr(event_obj, "user_id"):
        return
    user_id = str(event_obj.user_id)  # type: ignore[attr-defined]
    pending = _tg_2fa_pending.get(user_id)
    if pending is None:
        return  # 该用户没在等密码，跳过

    if time.monotonic() > pending.expires_at:
        logger.info(f"用户 {user_id} 的 2FA 等待窗口已过期, 清理 pending 并放行本条消息")
        _cleanup_user_pending(user_id)
        return

    from ..download import submit_2fa_password, wait_login_complete

    # 从 event 取纯文本（get_plaintext 是 MessageEvent 的方法，非 Matcher）
    password = event_obj.get_plaintext().strip() if hasattr(event_obj, "get_plaintext") else ""
    logger.debug(f"收到 {user_id} 的 2FA 密码候选，长度={len(password)}")
    if not password:
        # 空消息（纯图片/表情），忽略（等真正的密码）
        return
    if not _looks_like_2fa_password(password):
        # 不像密码：不消费、不阻断，让这条消息走正常解析/指令流程
        logger.debug(f"用户 {user_id} 在 2FA 窗口内发的消息不具密码形态, 放行")
        return

    handle = pending.handle
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


# ==================== 2FA pending 清理（防资源泄漏）====================
def _cleanup_user_pending(user_id: str) -> None:
    """清理指定用户的 pending 2FA handle：先终止 tdl 进程再 pop。

    在用户重新触发 ``tg登录`` 或等待窗口过期时调用，避免残留旧 handle。
    """
    pending = _tg_2fa_pending.pop(user_id, None)
    if pending is not None:
        _terminate_handle_safe(pending.handle)


def _cleanup_stale_2fa(max_age_seconds: float | None = None) -> int:
    """清理超过 ``max_age_seconds`` 未被消费的 2FA pending handle。

    由包根 ``__init__.py`` 的定时 job ``parser-tg-2fa-cleanup`` 周期调用（默认 5 分钟一次）。
    ``max_age_seconds`` 缺省取 :data:`_TG_2FA_CLEANUP_MAX_AGE`（TTL 的 5 倍 = 600s），
    与 job 显式传入的 600 等值。

    这是**兜底**路径：窗口正常过期由 ``_tg_password`` 自己判定 ``expires_at``；
    这里负责用户放弃/掉线/异常退出后没人会再消费的项。判定依据是 pending 自己的
    ``created_at``（monotonic 秒），而非 ``LoginQrHandle.created_at`` —— 后者是扫码
    会话创建时刻，比进入 2FA 等待更早，会把仍有效的等待误杀。

    Returns:
        本次清理掉的 handle 数量（用于日志统计）。
    """
    from ..download.tdl import _terminate_handle

    max_age = _TG_2FA_CLEANUP_MAX_AGE if max_age_seconds is None else max_age_seconds
    now = time.monotonic()
    stale_keys = [uid for uid, p in _tg_2fa_pending.items() if now - p.created_at > max_age]
    for uid in stale_keys:
        pending = _tg_2fa_pending.pop(uid, None)
        if pending is not None:
            try:
                _terminate_handle(pending.handle)
            except Exception as e:
                logger.warning(f"清理陈旧 2FA handle(user={uid}) 失败: {e!r}")
    return len(stale_keys)


def _terminate_handle_safe(handle: "LoginQrHandle") -> None:
    """安全终止 handle（吞异常，用于清理路径）。"""
    try:
        from ..download.tdl import _terminate_handle

        _terminate_handle(handle)
    except Exception as e:
        logger.warning(f"终止 2FA handle 失败: {e!r}")
