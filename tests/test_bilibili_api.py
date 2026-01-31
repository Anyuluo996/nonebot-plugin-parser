"""
测试 bilibili_api 库解析 opus ID
"""

import asyncio


async def test_bilibili_api_dynamic():
    """测试 bilibili_api.dynamic.Dynamic 是否能解析 opus ID"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "测试 bilibili_api.dynamic.Dynamic" + " "*16 + "║")
    print("╚" + "="*68 + "╝")

    # 由于 bilibili-api 安装失败，直接使用 httpx 模拟 Dynamic 接口
    import httpx

    dynamic_id = 1156587796127809560

    print(f"\n测试 ID: {dynamic_id}\n")

    # B站 dynamic API
    url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        "Referer": "https://m.bilibili.com/",
    }

    params = {
        "id": dynamic_id,
        "timezone_offset": -480,
    }

    print(f"请求 URL: {url}")
    print(f"参数: {params}\n")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers=headers)

        print(f"状态码: {response.status_code}")

        if response.status_code == 200:
            try:
                data = response.json()
                print(f"code: {data.get('code')}")
                print(f"message: {data.get('message')}")

                if data.get('code') == 0:
                    print(f"✅ API 请求成功")

                    # 保存响应
                    import json
                    with open('dynamic_api_response.json', 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"💾 数据已保存: dynamic_api_response.json")

                    # 显示数据结构
                    item = data.get('data', {}).get('item', {})
                    print(f"\nitem 类型: {item.get('type')}")
                    print(f"item 键: {list(item.keys())}")

                else:
                    print(f"❌ API 返回错误")

            except Exception as e:
                print(f"❌ JSON 解析失败: {e}")
        else:
            print(f"响应内容: {response.text[:500]}")


async def test_bilibili_api_opus():
    """测试 bilibili_api opus API"""

    print(f"\n{'='*70}")
    print("测试 bilibili_api opus API")
    print(f"{'='*70}\n")

    import httpx

    opus_id = 1156587796127809560

    # 尝试不同的 opus API
    apis = [
        ("opus detail API", "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail", {
            "id": opus_id,
        }),
        ("dynamic detail API", "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail", {
            "id": opus_id,
            "timezone_offset": -480,
        }),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        "Referer": "https://m.bilibili.com/",
    }

    for name, url, params in apis:
        print(f"{'='*70}")
        print(f"测试: {name}")
        print(f"{'='*70}")
        print(f"URL: {url}")
        print(f"参数: {params}\n")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params, headers=headers)

            print(f"状态码: {response.status_code}")

            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"code: {data.get('code')}")
                    print(f"message: {data.get('message')}")

                    if data.get('code') == 0:
                        print(f"✅ 成功")
                        item_data = data.get('data', {}).get('item', {})
                        print(f"item 键: {list(item_data.keys())}")
                    else:
                        print(f"❌ 失败")

                except Exception as e:
                    print(f"❌ JSON 解析失败: {e}")
            print()


async def main():
    await test_bilibili_api_opus()
    await test_bilibili_api_dynamic()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
