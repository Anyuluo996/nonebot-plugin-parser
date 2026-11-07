"""异步解析 API 包入口：HTTP 面注册、启动收尾、定时清理。

启用条件（全部满足才注册路由，缺任一仅 log 不报错，零影响现有链路）：
1. ``parser_api_enabled=True``
2. ``parser_api_token`` 与 ``parser_api_base_url`` 均已配置
3. 已安装 fastapi（extras: ``api``）且 driver 为 FastAPI（有 ``server_app``）

startup 固定顺序（顺序敏感，勿调整）：
1. ``mark_stale_interrupted()`` —— 先于清理 job 首跑：stale 行先拿到 finished_at，
   不会被清理误删成 404，轮询方拿到明确失败；
2. 配额计数器从 api_media 实际目录大小重建；
3. 单进程软检测（meta 表 pid 比对，不一致仅 warning）；
4. 配置校验 + fastapi 探测 → include_router。

清理 job（10 分钟）：TTL 过期行（queued/running 超时行同步 cancel）+ 无主媒体目录
（mtime 宽限，避开"目录已建、行未写"窗口）+ 限流桶瘦身。
"""

import time
import asyncio

from nonebot import logger, require, get_driver

from . import media, store, runner
from ..config import pconfig

_API_MEDIA_ORPHAN_GRACE = 600
"""无主媒体目录的 mtime 宽限（秒）：目录刚建、行还没写入的窗口内不清理。"""


@get_driver().on_startup
async def _startup() -> None:
    # 启用条件检查在前：未启用时不建 DB / 不动磁盘，其余功能零感知
    if not pconfig.api_enabled:
        return
    if not pconfig.api_token:
        logger.warning("parser_api_enabled=True 但未配置 parser_api_token，异步解析 API 不注册")
        return
    if not pconfig.api_base_url:
        logger.warning(
            "parser_api_enabled=True 但未配置 parser_api_base_url（须为 QQ 协议端可达地址），异步解析 API 不注册"
        )
        return

    from ..utils import is_module_available

    if not is_module_available("fastapi"):
        logger.warning("异步解析 API 需要 fastapi（uv add nonebot-plugin-parser[api]），未安装，不注册")
        return

    server_app = getattr(get_driver(), "server_app", None)
    if server_app is None:
        logger.warning("当前 driver 非 FastAPI（无 server_app），异步解析 API 不注册")
        return

    # 顺序敏感：DB 初始化 → stale 标记（必须先于清理 job 首跑，否则残留 running 行
    # 会被清理误删成 404 而非明确的失败）→ 配额重建 → 单进程检测
    #
    # 故障隔离：API 是可选功能，注册链任何一环失败只降级 API 自身，**绝不允许
    # 异常冒出 startup hook 炸掉整个 bot**（2026-11-08 部署实测：cleanup job
    # 参数错误让 uvicorn "Application startup failed"，容器崩溃循环）。
    # 功能性回归由 tests/others/test_startup_smoke.py 的真实 lifespan 冒烟
    # 测试兜底（断言 job/路由确实存在，而非仅无异常）。
    try:
        await store.init_db()
        await store.mark_stale_interrupted()
        # 重建是全目录 rglob+stat 遍历（配额默认 2GB 可达数千次 stat），丢线程池避免阻塞事件循环
        used = await asyncio.to_thread(media.quota.rebuild)
        if used:
            logger.info(f"api_media 配额计数器已重建: {used / 1024 / 1024:.1f}MB / {pconfig.api_media_quota_mb}MB")
        await store.check_single_process()

        # 懒导入 routes → fastapi；token/base_url 校验已过，签名子密钥此时可派生
        from . import routes

        routes.reset_sign_key()
        server_app.include_router(routes.build_router())
        _setup_cleanup_job()
    except Exception:
        logger.exception("异步解析 API 注册失败，本次启动不提供 API（其余功能不受影响）")
        return
    logger.info(f"异步解析 API 已注册: 前缀 {routes.API_PREFIX}，并发上限 {pconfig.api_max_concurrent}")


@get_driver().on_shutdown
async def _shutdown() -> None:
    await runner.shutdown()


def _setup_cleanup_job() -> None:
    """注册清理 job。仅在 API 已启用（startup 校验通过）时调用。"""
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler

    # 注意: scheduled_job（apscheduler 3.x）没有 replace_existing 参数 —— 那是 add_job
    # 的选项，误传会让 BaseScheduler.add_job 抛 "got multiple values" 直接炸掉整个
    # 启动流程（2026-11-08 部署实测翻车）。单进程 + 每次启动全新 scheduler，无需去重。
    @scheduler.scheduled_job("interval", minutes=10, id="parser-api-task-cleanup")
    async def _api_cleanup_job() -> None:
        try:
            await run_cleanup()
        except Exception:
            logger.exception("api 任务清理 job 异常")


async def run_cleanup() -> None:
    """TTL 清理：过期行（active 行同步 cancel）→ 无主媒体目录 → 限流桶瘦身。"""
    from . import routes

    expired = await store.cleanup_expired()
    for row in expired:
        task_id = row["task_id"]
        if row["status"] in ("queued", "running"):
            # 长期排队/执行超预算的行：同步取消执行协程，不能只删行
            await runner.cancel_task(task_id)
        await media.remove_task_dir(task_id)
    if expired:
        logger.info(f"api 清理：移除 {len(expired)} 个过期任务")

    # 无主媒体目录（行已不在 DB）：mtime 宽限避开「目录刚建、行未写」的竞态窗口
    known = await store.known_task_ids()
    now = time.time()
    orphan_count = 0
    for dir_ in media.iter_task_dirs():
        if dir_.name in known:
            continue
        try:
            if now - dir_.stat().st_mtime < _API_MEDIA_ORPHAN_GRACE:
                continue
        except OSError:
            continue
        await media.remove_task_dir(dir_.name)
        orphan_count += 1
    if orphan_count:
        logger.info(f"api 清理：移除 {orphan_count} 个无主媒体目录")

    routes.prune_rate_buckets()
