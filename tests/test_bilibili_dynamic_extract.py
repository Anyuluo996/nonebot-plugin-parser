"""
测试从 m.bilibili.com/dynamic 页面提取数据
"""

import asyncio
import json
import re


async def test_extract_dynamic_data():
    """测试从 m.bilibili.com/dynamic 页面提取数据"""
    import httpx

    dynamic_id = 1156587796127809560

    print(f"\n{'='*70}")
    print(f"测试提取 Dynamic 数据: {dynamic_id}")
    print(f"{'='*70}\n")

    url = f"https://m.bilibili.com/dynamic/{dynamic_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        text = response.text

        # 提取 __INITIAL_STATE__
        pattern = r'__INITIAL_STATE__\s*=\s*({.+?});'
        match = re.search(pattern, text)

        if match:
            data_str = match.group(1)
            data = json.loads(data_str)

            print("✅ 成功提取 __INITIAL_STATE__")
            print(f"\n数据结构:")
            print(f"  {list(data.keys())}")

            # 查找 opus 数据
            if "opus" in data:
                opus_data = data["opus"]
                print(f"\nopus 数据:")
                print(f"  keys: {list(opus_data.keys())}")

                if "detail" in opus_data:
                    detail = opus_data["detail"]
                    print(f"\n  detail:")
                    print(f"    keys: {list(detail.keys())}")

            # 保存完整数据到文件
            output_file = "dynamic_data.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"\n✅ 完整数据已保存到 {output_file}")
            print(f"   文件大小: {len(data_str)} 字符")

        else:
            print("❌ 未找到 __INITIAL_STATE__")


async def test_webkit_boundary_data():
    """测试提取 webkitBoundary 数据"""
    import httpx

    opus_id = 1156587796127809560

    print(f"\n{'='*70}")
    print(f"测试提取 webkitBoundary 数据: {opus_id}")
    print(f"{'='*70}\n")

    url = f"https://www.bilibili.com/opus/{opus_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        response = await client.get(url, headers=headers)

        print(f"状态码: {response.status_code}")
        print(f"Location: {response.headers.get('Location')}")

        if response.status_code == 302:
            print(f"\n⚠️  页面重定向到: {response.headers.get('Location')}")
            print(f"   这就是为什么无法获取数据的原因！")


async def test_compare_urls():
    """对比不同 URL 的响应"""
    import httpx

    id = 1156587796127809560

    urls = [
        f"https://www.bilibili.com/opus/{id}",
        f"https://m.bilibili.com/opus/{id}",
        f"https://www.bilibili.com/dynamic/{id}",
        f"https://m.bilibili.com/dynamic/{id}",
    ]

    print(f"\n{'='*70}")
    print(f"对比不同 URL 的响应")
    print(f"{'='*70}\n")

    headers_desktop = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    headers_mobile = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
    }

    for url in urls:
        use_mobile = "m.bilibili" in url
        headers = headers_mobile if use_mobile else headers_desktop

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                has_data = "__INITIAL_STATE__" in response.text
                status = "✅" if has_data else "❌"

                print(f"{status} {url}")
                print(f"   最终 URL: {response.url}")
                print(f"   状态码: {response.status_code}")
                print(f"   内容长度: {len(response.text)}")
                print(f"   包含数据: {'是' if has_data else '否'}")
                print()

        except Exception as e:
            print(f"❌ {url}")
            print(f"   错误: {e}")
            print()


async def main():
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*20 + "B站动态数据提取测试" + " "*20 + "║")
    print("╚" + "="*68 + "╝")

    await test_compare_urls()
    await test_webkit_boundary_data()
    await test_extract_dynamic_data()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
