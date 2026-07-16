"""L3：失败链接上报客户端。

把重试耗尽的失败记录 POST 到远程服务器（经 nginx 反代 + HTTPS）。
上报端点公开（无需 key），靠服务端白名单/限流/去重防滥用。
上报本身失败只 log warning，不影响主流程。
"""

from typing import Any

import httpx
from nonebot import logger

from .config import pconfig
from .failure_store import mark_reported

_REPORT_TIMEOUT = 10


async def report_failure_record(record: dict[str, Any]) -> bool:
    """上报单条失败记录到远程服务器。

    Returns:
        True: 上报成功（并标记 mark_reported）
        False: 未启用/配置不全/上报失败
    """
    if not pconfig.failure_report_enabled:
        return False
    url = pconfig.failure_report_url
    if not url:
        logger.warning("失败上报已启用但 url 未配置，跳过")
        return False

    payload = {
        "url": record.get("url"),
        "platform": record.get("platform"),
        "error": record.get("error"),
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
        "count": record.get("count", 1),
        "retries": record.get("retries", 0),
    }

    try:
        async with httpx.AsyncClient(timeout=_REPORT_TIMEOUT) as client:
            resp = await client.post(url.rstrip("/") + "/api/report", json=payload)
        if resp.status_code == 200:
            mark_reported(record.get("url", ""))
            return True
        logger.warning(f"失败上报 HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"失败上报异常（不影响主流程）: {e}")
    return False
