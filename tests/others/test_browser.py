"""browser 截图模块测试"""
import pytest


def test_is_browser_available_returns_bool():
    """is_browser_available 应返回 bool 且不抛异常"""
    from nonebot_plugin_parser.browser import is_browser_available

    assert isinstance(is_browser_available(), bool)


@pytest.mark.asyncio
async def test_screenshot_url_without_htmlrender(monkeypatch):
    """htmlrender 不可用时 screenshot_url 应抛 RuntimeError"""
    import nonebot_plugin_parser.browser as browser_mod

    monkeypatch.setattr(browser_mod, "is_browser_available", lambda: False)
    with pytest.raises(RuntimeError):
        await browser_mod.screenshot_url("https://example.com")


@pytest.mark.asyncio
async def test_screenshot_url_real():
    """真实截图（需 htmlrender + chromium + 网络），不可用则跳过

    CI 的 Test job 不预装浏览器，htmlrender 0.8 的自动下载在 CI 上耗时不稳定
    （~100MB 下载），故 CI 环境直接跳过，仅本地手测运行。
    """
    import os

    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        pytest.skip("CI 环境跳过真实截图（浏览器自动下载不稳定），本地手测运行")

    from nonebot_plugin_parser.browser import screenshot_url, is_browser_available

    if not is_browser_available():
        pytest.skip("nonebot_plugin_htmlrender 未安装")

    try:
        path, _title = await screenshot_url("https://example.com", full_page=False, extra_wait_ms=0)
    except Exception as e:
        pytest.skip(f"网络或浏览器不可用: {e}")

    assert path.exists()
    assert path.suffix == ".png"
