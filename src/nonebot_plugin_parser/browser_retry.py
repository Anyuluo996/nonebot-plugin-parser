"""Playwright 浏览器渲染的传输断连兜底重试。

nonebot_plugin_htmlrender 维护一个全局浏览器单例（`_browser`），但其
`get_browser()` 仅用 `_browser.is_connected()` 判断可用性。当浏览器子进程
崩溃（OOM / 信号 / 容器资源限制）时，Python 侧 `is_connected()` 仍可能返回
True，而底层 asyncio 传输（`WriteUnixTransport`）已经关闭，下一次
`browser.new_page()` 会抛出：

    RuntimeError: Browser.new_page: unable to perform operation on
    <WriteUnixTransport closed=True ...>; the handler is closed

本模块提供 `with_browser_retry`，在捕获此类"传输已关闭"错误时强制
`shutdown_browser()` + `init_browser()` 重启浏览器，然后重试一次。
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
    """强制重启 nonebot_plugin_htmlrender 的全局浏览器实例。"""
    try:
        from nonebot_plugin_htmlrender import init_browser, shutdown_browser
    except ImportError:
        logger.error("nonebot_plugin_htmlrender 未安装，无法重启浏览器")
        return

    logger.warning("检测到浏览器传输已关闭，正在强制重启 Playwright…")
    try:
        await shutdown_browser()
    except Exception as e:
        logger.debug(f"shutdown_browser 抑制异常: {e!r}")
    try:
        await init_browser()
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
        func: 无参异步可调用对象，内部真正发起一次 `template_to_pic` / `get_new_page` 调用。
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
