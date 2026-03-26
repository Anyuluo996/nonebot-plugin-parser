"""测试 NGA 图片 curl_cffi 下载（不依赖 nonebot 初始化）"""
import asyncio
import sys
from pathlib import Path

NGA_IMG_URL = "https://img.nga.178.com/attachments/mon_202603/23/-7s28Q2x-6z2iZpT1kShs-100.jpg"
HEADERS = {
    "Referer": "https://nga.178.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/110.0.5481.178 Safari/537.36"
    ),
}

# 直接复制被测函数的逻辑（隔离测试，不触发 nonebot 初始化）
_REFERRER_MAP = {
    "img.nga.178.com": "https://nga.178.com/",
}
_CURL_ONLY_DOMAINS = frozenset({"img.nga.178.com"})


def _auto_referer(url: str):
    from urllib.parse import urlparse
    try:
        netloc = urlparse(url).netloc.rsplit(":", 1)[0]
        return _REFERRER_MAP.get(netloc)
    except Exception:
        return None


def _use_curl(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        netloc = urlparse(url).netloc.rsplit(":", 1)[0]
        return netloc in _CURL_ONLY_DOMAINS
    except Exception:
        return False


async def test_auto_referer():
    """测试域名 Referer 匹配"""
    assert _auto_referer(NGA_IMG_URL) == "https://nga.178.com/"
    assert _auto_referer("https://img.nga.178.com:443/abc.jpg") == "https://nga.178.com/"
    assert _auto_referer("https://example.com/img.jpg") is None
    print("[PASS] _auto_referer")


async def test_use_curl():
    """测试 curl 路由"""
    assert _use_curl(NGA_IMG_URL) is True
    assert _use_curl("https://img.nga.178.com/abc.jpg") is True
    assert _use_curl("https://bilibili.com/video.jpg") is False
    print("[PASS] _use_curl")


async def test_curl_cffi_single():
    """测试 curl_cffi 单次下载 NGA 图片"""
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome110") as s:
        resp = await s.get(NGA_IMG_URL, headers=HEADERS, allow_redirects=True)
        assert resp.status_code == 200, f"期望 200, 实际 {resp.status_code}"
        assert len(resp.content) > 1000, f"内容过小: {len(resp.content)} bytes"
        print(f"[PASS] curl_cffi 单次下载: status={resp.status_code}, size={len(resp.content)}")


async def test_curl_cffi_retry():
    """测试 curl_cffi 重试逻辑（模拟 567 场景）"""
    from curl_cffi.requests import AsyncSession, RequestsError
    import random

    # 模拟：第一次返回 567，后面成功
    call_count = 0

    original_get = AsyncSession.get

    class FakeResponse:
        status_code = 200
        content = b"fake_image_data" * 100

    async def fake_get(self, url, **kwargs):
        nonlocal call_count
        call_count += 1
        # 第一次返回 567 响应类（模拟被限制）
        if call_count == 1:
            resp = type("Fake567", (), {"status_code": 567, "content": b""})()
            return resp
        return FakeResponse()

    AsyncSession.get = fake_get

    try:
        from nonebot_plugin_parser.download import _download_by_curl
        from nonebot_plugin_parser.utils import safe_unlink

        test_path = Path("/tmp/test_retry.jpg")
        await safe_unlink(test_path)

        # 注入 max_retries=2 加快测试
        result = await _download_by_curl(NGA_IMG_URL, test_path, HEADERS, max_retries=2)

        assert call_count == 2, f"期望重试 1 次，实际 {call_count - 1} 次"
        assert result.exists(), "下载后文件不存在"
        print(f"[PASS] curl_cffi 重试逻辑: 重试了 {call_count - 1} 次，文件大小={result.stat().st_size}")
        await safe_unlink(result)
    finally:
        AsyncSession.get = original_get


async def test_httpx_blocked():
    """验证 httpx 被 NGA 拦截（对照）"""
    from httpx import AsyncClient

    async with AsyncClient(timeout=10, verify=False) as c:
        resp = await c.get(NGA_IMG_URL, headers=HEADERS)
        print(f"[INFO] httpx 状态码: {resp.status_code} (预期 567 = 被拦截)")


async def main():
    print("=" * 60)
    print("测试: NGA 图片 curl_cffi 下载")
    print("=" * 60)

    await test_auto_referer()
    await test_use_curl()
    await test_curl_cffi_single()
    await test_httpx_blocked()
    await test_curl_cffi_retry()

    print()
    print("=" * 60)
    print("所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
