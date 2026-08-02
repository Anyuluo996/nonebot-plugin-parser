"""L2：失败链接定时重试。

apscheduler 定时扫描本地失败记录，对每条重新解析：
- 成功 → mark_success（静默删除）
- 失败 → mark_retried；若 retries>=max 且 L3 启用 → report_failure_record

重试在定时任务里跑，无用户消息上下文，无法把成功结果回推原用户。
"""

import asyncio

from nonebot import logger

from .config import pconfig
from .parsers import BaseParser
from .exception import ParseException
from .failure_store import mark_retried, mark_success, mark_reported, get_retryable_failures
from .failure_reporter import report_failure_record

# 单条重试的并发上限，避免一次性重试大量失败链接打爆目标站点
_MAX_CONCURRENT = 3
# 单条重试超时（秒），防止某些 parser 的网络请求挂起
_PER_RETRY_TIMEOUT = 60


async def _retry_one(record: dict) -> None:
    """重试单条失败记录。"""
    url = record.get("url", "")
    if not url:
        return

    # 找到能处理该 URL 的 parser，重建 (keyword, searched)
    parser = _find_parser_for_url(url)
    if parser is None:
        # 无 parser 能匹配（如平台已禁用），标记重试失败但不反复尝试
        mark_retried(url, "no parser matches this url")
        return

    try:
        keyword, searched = parser.search_url(url)
    except ParseException:
        mark_retried(url, "url no longer matches any parser pattern")
        return

    try:
        await asyncio.wait_for(parser.parse(keyword, searched), timeout=_PER_RETRY_TIMEOUT)
        # 成功：静默删除
        mark_success(url)
        logger.debug(f"失败链接重试成功，已删除: {url}")
    except Exception as e:
        err = f"{type(e).__name__}: {e!s}"
        # mark_retried 内部对 _failures 的原记录 retries+=1（与本次传入的 record 拷贝解耦）。
        # 上报所需的最新 retries/error 从 _failure 取回，避免手动递增产生双倍计数。
        mark_retried(url, err)
        from . import failure_store

        latest = failure_store.get_failures()
        rec = next((r for r in latest if r.get("url") == url), record)
        # 达到上限 → 上报
        if int(rec.get("retries", 0)) >= pconfig.failure_retry_max:
            if pconfig.failure_report_enabled:
                ok = await report_failure_record(rec)
                if not ok:
                    # 上报未启用/失败：仍标记 reported 避免无限重试
                    mark_reported(url)
            else:
                mark_reported(url)
        logger.debug(f"失败链接重试仍失败({rec.get('retries', 0)}/{pconfig.failure_retry_max}): {url} - {err}")


def _find_parser_for_url(url: str) -> BaseParser | None:
    """遍历已注册 parser 实例，找第一个 search_url 能匹配该 url 的。

    直接复用 ``matchers.KEYWORD_PARSER_MAP`` 里运行态的 parser 实例
    （与用户消息走的是同一批），而非另起 ``parser_cls()``。这样凭据缓存
    （如 BilibiliParser._credential）、cookies 文件、连接池在主流程与重试
    流程间共享，避免重复加载凭据 / 并发写同一 cookies 文件。
    """
    # 懒导入避免 matchers 与 failure_retry 之间的循环导入
    from .matchers import KEYWORD_PARSER_MAP

    for parser in KEYWORD_PARSER_MAP.values():
        try:
            parser.search_url(url)  # 不匹配会抛 ParseException
            return parser
        except ParseException:
            continue
    return None


async def run_failure_retry() -> None:
    """定时任务入口：扫描并重试所有可重试的失败记录。

    由 apscheduler 在 event loop 中调用。配置关闭时直接返回。
    """
    if not pconfig.failure_retry_enabled:
        return

    max_retries = pconfig.failure_retry_max
    pending = get_retryable_failures(max_retries)
    if not pending:
        return

    logger.debug(f"失败链接重试：扫描到 {len(pending)} 条待重试")
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _bounded(record: dict) -> None:
        async with sem:
            try:
                await _retry_one(record)
            except Exception as e:
                # _retry_one 内部已处理业务异常，这里兜底防调度崩
                logger.warning(f"重试任务异常（不应发生）: {e}")

    await asyncio.gather(*[_bounded(r) for r in pending])
