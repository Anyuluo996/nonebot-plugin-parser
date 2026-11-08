"""异步解析 API 的任务执行引擎。

职责：校验 → 去重 → 建任务 → 信号量限流执行 → 序列化转存 → 写库。
复用与 QQ 消息链路完全相同的解析管线（``find_parser_for_url`` 共享运行态
parser 实例的凭据/连接池；``parse_with_retry`` 自带顶层超时与指数退避重试，
且其失败**不写** failure_store、不上报 L3 —— 失败记录只发生在 matcher 层，
API 任务的失败不会污染 QQ 链路的失败重试队列）。

取消语义（DELETE / shutdown / TTL 清理共用）：
- 经 :data:`registry` 取消 asyncio.Task 并 **await 真正结束**，再写 cancelled
  状态、删媒体目录、释放配额 —— 与 runner 的清理严格串行，DELETE 后立刻重新
  提交同一 URL 不会与旧任务的清理交错；
- runner 被 cancel 时可能仍有 parser 惰性下载 Task 在跑（它们不是本协程的子
  任务）：best-effort cancel；这些下载只写插件自身缓存目录，残料由既有的每日
  缓存清理兜底（与 matcher 层顶层超时的既有取舍一致）。
"""

import asyncio
import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from nonebot import logger

from . import media, store, rcache, serialize
from ..config import pconfig
from ..matchers import find_parser_for_url
from ..exception import TipException, IgnoreException
from ..parse_retry import parse_with_retry

if TYPE_CHECKING:
    from re import Match

    from ..parsers import BaseParser, ParseResult

_MAX_ACTIVE_TASKS = 100
"""queued+running 总量硬上限（防队列被写穿），超限 429。"""


def normalize_url(url: str) -> str:
    """去重键规范化：去首尾空白与 fragment、host 小写。

    不做更多激进归一（去 tracking 参数等）——不同参数可能指向不同内容，
    误合并比漏去重更糟。
    """
    url = url.strip()
    parts = urlparse(url)
    host = (parts.netloc or "").lower()
    path = parts.path or ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host}{path}{query}"


def validate_url(url: str) -> tuple["BaseParser", str, "Match[str]"]:
    """提交时的零抓取校验：scheme 白名单 + 必须有 parser 能匹配。

    返回 ``(parser, keyword, searched)``；不满足抛 :class:`ValueError`
    （routes 层映射为 422）。校验阶段不发起任何网络请求 —— 解析器均为
    平台专用 pattern，无 generic 抓取面，这就是 SSRF 边界。
    """
    if not url or urlparse(url).scheme.lower() not in ("http", "https"):
        raise ValueError("url scheme 必须为 http/https")
    parser = find_parser_for_url(url)
    if parser is None:
        raise ValueError("不支持的平台或链接格式")
    try:
        keyword, searched = parser.search_url(url)
    except Exception as e:
        raise ValueError("不支持的平台或链接格式") from e
    return parser, keyword, searched


registry: dict[str, asyncio.Task] = {}
"""task_id → 执行协程。DELETE/shutdown/cleanup 经它取消并 await 任务结束。"""


async def submit_task(url: str) -> tuple[dict, bool]:
    """提交任务，返回 ``(任务行, 是否新建)``。

    判定顺序：去重（queued/running）→ 零抓取校验 → 结果缓存复用 → 队列上限。
    去重命中返回既有行（created=False）—— 队列满时同 URL 重复提交仍能拿回既有
    task_id（轮询方重试不受 429 误伤）。零抓取校验先行：被禁用平台/垃圾输入
    不白发缓存请求。succeeded 后的同 URL 提交：结果缓存命中则直接复用（不占
    解析并发/队列额度，行带 cache_hit=1）；未命中才重新解析。校验失败抛
    :class:`ValueError`（422），超限抛 :class:`QueueFullError`（429）。

    去重是 best-effort（并发同 URL 提交存在查-插窗口，无唯一约束兜底）：
    最坏结果是同链接重复解析一次，属可接受代价，不为此引入唯一索引复杂度。
    """
    normalized = normalize_url(url)
    if existing := await store.find_active_by_url(normalized):
        return existing, False

    parser, keyword, searched = validate_url(url)

    # 结果缓存复用：媒体已在 get() 内物化到本任务目录，载荷零改写直接建行。
    # 拉取先于建行：配额不足/对端失败在 get 内部回滚并返回 None（绝不 500）；
    # 建行失败 → remove_task_dir 回滚 → 落到原解析路径。
    task_id = secrets.token_urlsafe(16)
    cached = await rcache.get(normalized, task_id)
    if cached is not None:
        payload, staged, staged_bytes = cached
        try:
            await store.create_task_with_result(
                task_id, url.strip(), normalized, parser.platform.name, payload, staged, staged_bytes
            )
            row = await store.get_task(task_id)
            if row is not None:
                logger.info(
                    f"api 任务结果缓存命中: task={task_id[:8]}… platform={parser.platform.name} staged={len(staged)}"
                )
                return row, True
        except Exception:
            logger.warning("api 任务缓存复用建行失败，回滚后走原解析", exc_info=True)
        await media.remove_task_dir(task_id, staged_bytes)
        # 换新 task_id 走原解析（防建行部分成功后主键冲突）
        task_id = secrets.token_urlsafe(16)

    if await store.count_active() >= _MAX_ACTIVE_TASKS:
        raise QueueFullError

    await store.create_task(task_id, url.strip(), normalized)
    # 先读行再调度：create_task 让出循环后 _run 可能已把行翻到 running，
    # 202 信封里的 status 必须确定性为 queued
    row = await store.get_task(task_id)
    if row is None:  # 理论不可达（刚插入）；防御性兜底
        raise RuntimeError("task row vanished immediately after insert")
    atask = asyncio.create_task(_run(task_id, normalized, parser, keyword, searched))
    registry[task_id] = atask
    return row, True


class QueueFullError(Exception):
    """queued+running 任务数达硬上限。"""


_PARSE_SEM = asyncio.Semaphore(pconfig.api_max_concurrent)
"""解析并发闸门（模块级共享，覆盖解析+序列化全程）。"""


async def _run(task_id: str, url_norm: str, parser: "BaseParser", keyword: str, searched: "Match[str]") -> None:
    """单任务执行体。状态流转 queued → running → succeeded/failed；取消由调用方写 cancelled。"""
    result: "ParseResult | None" = None
    try:
        async with _PARSE_SEM:
            # 写前复查：行被 DELETE 判定取消 → 放弃执行（调用方已接管清场）
            if not await store.set_running_if_queued(task_id, parser.platform.name):
                registry.pop(task_id, None)
                return
            logger.info(f"api 任务开始解析: task={task_id[:8]}… platform={parser.platform.name}")
            result = await parse_with_retry(parser, keyword, searched)
            payload, staged, staged_bytes, degraded = await serialize.build_result(result, task_id)
            if not await store.set_result(task_id, payload, staged, staged_bytes):
                # 行已被 DELETE 删掉（或已被清理 job 处理）：清掉本轮已转存的文件
                await media.remove_task_dir(task_id, staged_bytes)
                logger.debug(f"api 任务结果写入时行已不存在（已取消/清理）: task={task_id[:8]}…")
            else:
                logger.info(
                    f"api 任务解析成功: task={task_id[:8]}… platform={parser.platform.name} staged={len(staged)}"
                )
                # 写结果缓存（完整成功才入缓存；降级产物由 put 内部跳过）。整体
                # 吞异常：缓存只是加速，绝不影响解析结果交付
                await rcache.put(url_norm, payload, staged, staged_bytes, task_id, keyword, degraded=degraded)
    except TipException as e:
        # Tip 是设计为用户可见的语义性提示（已下架/需登录等），原文案可安全返回
        await store.set_failed(task_id, "tip", e.message)
    except IgnoreException:
        await store.set_failed(task_id, "parse_error", "内容不可用")
    except asyncio.TimeoutError:
        await store.set_failed(task_id, "timeout", "解析超时")
    except media.MediaQuotaExceeded:
        # 配额超限：清掉本轮已部分转存的文件（失败不写 result，错误只给归一化文案）
        await media.remove_task_dir(task_id)
        await store.set_failed(task_id, "quota", "媒体转存配额超限")
        logger.warning(f"api 任务媒体转存配额超限: task={task_id[:8]}…")
    except asyncio.CancelledError:
        # DELETE/shutdown/cleanup 已接管收尾（写 cancelled + 删目录 + 释放配额）；
        # 这里只取消仍在跑的惰性下载后原样上抛
        if result is not None:
            result.cancel_pending_downloads()
        raise
    except Exception:
        # 详细异常（可能含路径/凭据片段/带参 URL）只进日志，不给调用方。
        # 清掉本轮已部分转存的文件：failed 行没有 staged 清单、媒体端点不可达，
        # 留着只会白占配额直到 TTL（连续失败可能把配额吃穿饿死新任务）
        await media.remove_task_dir(task_id)
        logger.exception(f"api 任务解析失败: task={task_id[:8]}… platform={parser.platform.name}")
        await store.set_failed(task_id, "parse_error", "解析失败")
    finally:
        registry.pop(task_id, None)


async def cancel_task(task_id: str) -> None:
    """取消注册表里的执行协程并等待其真正结束（DELETE/shutdown/cleanup 共用）。"""
    atask = registry.pop(task_id, None)
    if atask is None or atask.done():
        return
    atask.cancel()
    try:
        await atask
    except asyncio.CancelledError:
        pass


async def shutdown() -> None:
    """shutdown 钩子：取消全部执行协程，把仍处 active 的行标记 cancelled。

    竞态收口与 DELETE 同款：cancel 等待期间任务可能已自行完成（set_cancelled
    条件写不覆盖终态），重读行只对仍 active 的行写 cancelled + 清目录；
    已终态的行保持行与媒体一致，交给 TTL 清理 —— 否则会留下「succeeded 行 +
    媒体 404」的错配，重启后轮询方仍能拿到该行。
    """
    task_ids = list(registry.keys())
    for task_id in task_ids:
        await cancel_task(task_id)
        row = await store.get_task(task_id)
        if row is None:
            continue
        if row["status"] in ("queued", "running"):
            await store.set_cancelled(task_id)
            await media.remove_task_dir(task_id)
    if task_ids:
        logger.info(f"api shutdown：取消 {len(task_ids)} 个未完成任务")
