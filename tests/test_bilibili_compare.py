"""
对比不同请求方式的差异
"""

import asyncio
from curl_cffi import requests as curl_requests
import httpx


async def test_httpx_vs_curl():
    """对比 httpx 和 curl_cffi 的差异"""

    opus_id = 1156587796127809560
    url = f"https://m.bilibili.com/opus/{opus_id}"

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*20 + "对比测试: httpx vs curl" + " "*20 + "║")
    print("╚" + "="*68 + "╝")

    # 测试1: httpx 默认 headers
    print(f"\n{'='*70}")
    print("测试1: httpx 默认 headers")
    print(f"{'='*70}\n")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url)
        print(f"URL: {url}")
        print(f"最终 URL: {response.url}")
        print(f"状态码: {response.status_code}")
        print(f"User-Agent: {response.request.headers.get('User-Agent')}")
        print(f"内容长度: {len(response.text)}")
        has_data = "__INITIAL_STATE__" in response.text
        print(f"包含数据: {'✅' if has_data else '❌'}")

    # 测试2: httpx 移动端 headers
    print(f"\n{'='*70}")
    print("测试2: httpx 移动端 headers")
    print(f"{'='*70}\n")

    mobile_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
    }

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=mobile_headers)
        print(f"URL: {url}")
        print(f"最终 URL: {response.url}")
        print(f"状态码: {response.status_code}")
        print(f"User-Agent: {response.request.headers.get('User-Agent')}")
        print(f"内容长度: {len(response.text)}")
        has_data = "__INITIAL_STATE__" in response.text
        print(f"包含数据: {'✅' if has_data else '❌'}")

    # 测试3: curl_cffi chrome
    print(f"\n{'='*70}")
    print("测试3: curl_cffi chrome")
    print(f"{'='*70}\n")

    response = curl_requests.get(url, impersonate="chrome", timeout=30, allow_redirects=True)
    print(f"URL: {url}")
    print(f"最终 URL: {response.url}")
    print(f"状态码: {response.status_code}")
    print(f"User-Agent: {response.request.headers.get('User-Agent')}")
    print(f"内容长度: {len(response.text)}")
    has_data = "__INITIAL_STATE__" in response.text
    print(f"包含数据: {'✅' if has_data else '❌'}")

    # 测试4: curl_cffi 不使用 impersonate
    print(f"\n{'='*70}")
    print("测试4: curl_cffi 不使用 impersonate")
    print(f"{'='*70}\n")

    response = curl_requests.get(url, timeout=30, allow_redirects=True)
    print(f"URL: {url}")
    print(f"最终 URL: {response.url}")
    print(f"状态码: {response.status_code}")
    print(f"User-Agent: {response.request.headers.get('User-Agent')}")
    print(f"内容长度: {len(response.text)}")
    has_data = "__INITIAL_STATE__" in response.text
    print(f"包含数据: {'✅' if has_data else '❌'}")

    # 测试5: 不允许重定向
    print(f"\n{'='*70}")
    print("测试5: httpx 不允许重定向")
    print(f"{'='*70}\n")

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.get(url, headers=mobile_headers)
        print(f"URL: {url}")
        print(f"状态码: {response.status_code}")
        print(f"Location: {response.headers.get('Location')}")
        print(f"User-Agent: {response.request.headers.get('User-Agent')}")


async def main():
    await test_httpx_vs_curl()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
