"""聊天链路的任务记录（轻量方案）。

bot 聊天解析与 API 提交共用同一张任务表：聊天来源（``source='chat'``）的行
只记**状态与失败原因**，不存结果载荷（消息已直接发进群，存 results 意义不大，
也避免每条消息做媒体转存挤占配额）。效果：历史列表 / ``?url=`` 按链接直查
能看到「这条链接何时被聊天解析过、成功与否、失败原因」。

硬约束：**记录永远不能影响聊天解析本身** —— 两个入口都整体吞异常，失败仅
debug 留痕（与 failure_store「不阻断主流程」同一原则）。
"""

import secrets

from nonebot import logger

from . import store
from .runner import normalize_url


async def record_chat_start(url: str, platform: str) -> str | None:
    """聊天解析开始：写入 running 行，返回 task_id（失败返回 None，绝不抛出）。"""
    try:
        task_id = secrets.token_urlsafe(16)
        await store.create_task(
            task_id, url, normalize_url(url), platform=platform, status="running", source="chat", started=True
        )
        return task_id
    except Exception as e:
        logger.debug(f"聊天任务记录失败（不影响解析）: {e!r}")
        return None


async def record_chat_result(
    task_id: str | None,
    ok: bool,
    error_kind: str | None = None,
    error: str | None = None,
) -> None:
    """聊天解析终态：成功置 succeeded；失败置 failed 并记录归一化原因 + 详情。

    ``error`` 为给维护者看的失败原因（如 ``ParseException: xxx`` / 发送段异常），
    区别于 API 调用方拿到的脱敏文案。
    """
    if not task_id:
        return
    try:
        if ok:
            await store.set_succeeded(task_id)
        else:
            await store.set_failed(task_id, error_kind, error)
    except Exception as e:
        logger.debug(f"聊天任务终态记录失败（不影响解析）: {e!r}")
