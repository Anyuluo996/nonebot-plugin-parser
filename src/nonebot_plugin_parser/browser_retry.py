"""Playwright 浏览器渲染的传输断连兜底重试。

nonebot_plugin_htmlrender 0.8 的 playwright provider 用 ExecutionLeaseProvider
管理全局浏览器 lease，`_acquire()` 时若 lease 已死会自动重建；但其存活判据
仍是 `browser.is_connected()`——浏览器子进程崩溃（OOM / 信号 / 容器资源限制）
时 Python 侧 `is_connected()` 可能仍返回 True，而底层 asyncio 传输
（`WriteUnixTransport`）已经关闭，下一次操作会抛出：

    RuntimeError: Browser.new_page: unable to perform operation on
    <WriteUnixTransport closed=True ...>; the handler is closed

本模块提供 `with_browser_retry`，在捕获此类"传输已关闭"错误时强制重建渲染
Application 并重试一次（nonebot-plugin-htmlrender 0.8 API）。
"""

from typing import TypeVar
from collections.abc import Callable, Awaitable

from nonebot import logger

T = TypeVar("T")

# 触发重启的关键词（大小写不敏感匹配异常字符串）
# - "transport" / "handler is closed": asyncio 传输已关闭
# - "target closed" / "browser has disconnected": Playwright 浏览器掉线
# - "connection closed": 连接关闭
_BROWSER_DEAD_MARKERS = (
    "transport",
    "handler is closed",
    "target closed",
    "browser has disconnected",
    "browser closed",
    "connection closed",
)


def _is_browser_dead_error(exc: BaseException) -> bool:
    """异常是否表明全局浏览器实例已经死亡、需要重启。"""
    msg = str(exc).lower()
    return any(marker in msg for marker in _BROWSER_DEAD_MARKERS)


async def _restart_global_browser() -> None:
    """强制重建 nonebot_plugin_htmlrender 的渲染 Application。

    htmlrender 0.8 的 Application 在 aclose() 后不可复用（再 startup() 会
    永久拒绝），重启 = aclose 旧实例 → 重置进程默认值（bootstrap 安装的
    factory 会重新构建全新组合，浏览器子进程随之重建）→ startup()。
    """
    from nonebot_plugin_htmlrender import (
        get_default_application,
        set_default_application,
    )

    logger.warning("检测到浏览器传输已关闭，正在强制重启 Playwright…")
    app = get_default_application()
    try:
        await app.aclose()  # 幂等；浏览器已死时 close 可能抛错，抑制
    except Exception as e:
        logger.debug(f"aclose 抑制异常: {e!r}")
    set_default_application(None)
    new_app = get_default_application()
    try:
        await new_app.startup()
    except Exception:
        logger.exception("重启浏览器失败")
        raise
    logger.success("Playwright 浏览器已重启")


async def with_browser_retry(
    func: Callable[[], Awaitable[T]],
    *,
    retries: int = 1,
) -> T:
    """执行一次浏览器渲染调用，传输断连时自动重启浏览器并重试。

    Args:
        func: 无参异步可调用对象，内部真正发起一次 `render_template` / `page()` 调用。
        retries: 遇到传输断连时的最大重试次数（每次重试前都会重启浏览器）。

    Raises:
        最后一次重试仍失败的原始异常。
    """
    attempt = 0
    while True:
        try:
            return await func()
        except Exception as exc:
            attempt += 1
            if attempt > retries or not _is_browser_dead_error(exc):
                # 非浏览器死亡类异常，或重试次数用尽，原样抛出
                raise
            logger.warning(
                f"浏览器调用失败（疑似传输断连，第 {attempt} 次）：{exc!r}，"
                f"将重启浏览器后重试（剩余 {retries - attempt + 1} 次）"
            )
            await _restart_global_browser()
