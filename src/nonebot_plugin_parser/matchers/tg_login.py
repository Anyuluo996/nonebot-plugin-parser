"""Telegram 解析授权白名单 + tdl 扫码登录(含 2FA 两步验证)。

从 ``matchers/__init__.py`` 拆出。

- Telegram 解析需消耗本机 tdl 会话，仅 SUPERUSER 可用；
  SUPERUSER 可通过 ``tg授权``/``tg取消授权``/``tg白名单`` 授权其他用户。
- ``tg登录`` 触发 tdl 扫码，账号开启两步验证(2FA)时把 ``LoginQrHandle`` 存入
  ``_tg_2fa_pending``，由独立的 ``_tg_password`` message matcher 接收密码。

为防止「用户触发 2FA 后不发密码」导致 pty fd/子进程/读线程泄漏，
``_tg_2fa_pending`` 每项记录 ``created_at``（来自 ``LoginQrHandle``），
由包根 ``__init__.py`` 的定时 job ``parser-tg-2fa-cleanup`` 周期性调用
``_cleanup_stale_2fa`` 清理超时项。
"""

from typing import TYPE_CHECKING

from nonebot import logger, on_command, on_message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher, current_event
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER

from .admin import _normalize_user_id
from .filter import add_tg_whitelist, get_tg_whitelist, remove_tg_whitelist
from ..helper import UniHelper, UniMessage

if TYPE_CHECKING:
    from ..download import LoginQrHandle


# 模块级 2FA 密码等待状态：{user_id: LoginQrHandle}
# tg登录 检测到 2FA 时写入，tg密码 matcher 消费后删除；
# 超时未消费的项由 _cleanup_stale_2fa 清理（防 pty fd/子进程/读线程泄漏）。
_tg_2fa_pending: dict[str, "LoginQrHandle"] = {}


# ==================== Telegram 解析授权白名单 ====================
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


# ==================== tdl 扫码登录 + 2FA ====================
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


# ==================== 2FA pending 清理（防资源泄漏）====================
def _cleanup_user_pending(user_id: str) -> None:
    """清理指定用户的 pending 2FA handle：先终止 tdl 进程再 pop。

    在用户重新触发 ``tg登录`` 时调用，避免残留旧 handle。
    """
    handle = _tg_2fa_pending.pop(user_id, None)
    if handle is not None:
        _terminate_handle_safe(handle)


def _cleanup_stale_2fa(max_age_seconds: float = 600.0) -> int:
    """清理超过 ``max_age_seconds`` 未被消费的 2FA pending handle。

    由包根 ``__init__.py`` 的定时 job ``parser-tg-2fa-cleanup`` 周期调用（默认 5 分钟一次）。
    判定依据 ``LoginQrHandle.created_at``（monotonic 秒）。

    Returns:
        本次清理掉的 handle 数量（用于日志统计）。
    """
    import time

    from ..download.tdl import _terminate_handle

    now = time.monotonic()
    stale_keys = [uid for uid, h in _tg_2fa_pending.items() if now - h.created_at > max_age_seconds]
    for uid in stale_keys:
        handle = _tg_2fa_pending.pop(uid, None)
        if handle is not None:
            try:
                _terminate_handle(handle)
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
