"""浏览器截图模块

复用 nonebot_plugin_htmlrender 已管理的全局 playwright driver，以手机模式对网页截图。
用于短链重定向到无解析 handler 的子站（B站会员购商城等）时的兜底展示。
"""

from typing import Any
from pathlib import Path

from nonebot import logger

from .utils import generate_file_name, is_module_available
from .config import pconfig

# 手机模式 context 参数（参考 tests/inspect_bili_elements.py）
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
_MOBILE_PAGE_KWARGS: dict[str, Any] = {
    "viewport": {"width": 414, "height": 896},
    "is_mobile": True,
    "has_touch": True,
    "user_agent": _MOBILE_UA,
    "device_scale_factor": 2,
}


def is_browser_available() -> bool:
    """nonebot_plugin_htmlrender (playwright) 是否可用"""
    return is_module_available("nonebot_plugin_htmlrender")


async def screenshot_url(
    url: str,
    *,
    full_page: bool = False,
    extra_wait_ms: int = 1500,
    timeout_ms: int = 30_000,
) -> tuple[Path, str]:
    """以手机模式打开 url 并截图。

    复用 nonebot_plugin_htmlrender 已管理的全局 playwright driver（不自起浏览器）。

    Args:
        url: 目标网页 URL
        full_page: 是否整页截图，默认 False（仅首屏）
        extra_wait_ms: 页面 networkidle 后额外等待毫秒数，用于 SPA 渲染
        timeout_ms: 页面加载超时毫秒数

    Returns:
        (截图文件路径, 页面标题)

    Raises:
        RuntimeError: nonebot_plugin_htmlrender 未安装
        Exception: 页面加载或截图失败（由调用方捕获转换）
    """
    import aiofiles

    if not is_browser_available():
        raise RuntimeError("nonebot_plugin_htmlrender 未安装，无法截图")

    from nonebot_plugin_htmlrender import get_new_page

    from .browser_retry import with_browser_retry

    logger.info(f"开始截图（手机模式）: {url}")

    async def _do_screenshot() -> tuple[bytes, str]:
        # 注: nonebot-plugin-htmlrender 0.7.x 的 get_new_page 返回类型标注为
        # AsyncIterator[object] (库自身类型缺陷)，但实际 yield 的是 playwright Page。
        # 用 cast 把 page 收窄到 Page，避免 basedpyright 误报 attribute 访问。
        from typing import cast

        from playwright.async_api import Page

        async with get_new_page(**_MOBILE_PAGE_KWARGS) as raw_page:
            page = cast(Page, raw_page)
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            if extra_wait_ms > 0:
                await page.wait_for_timeout(extra_wait_ms)
            title = (await page.title()) or ""
            img = await page.screenshot(full_page=full_page, type="png")
        return img, title

    # 浏览器传输断连时自动重启并重试
    img, title = await with_browser_retry(_do_screenshot)

    # 强制 .png 扩展名：URL path 可能带 .html 等后缀，generate_file_name 会原样保留
    file_name = f"{Path(generate_file_name(url)).stem}.png"
    file_path = pconfig.cache_dir / file_name
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(img)
    logger.success(f"截图完成: {file_path.name}")
    return file_path, title
