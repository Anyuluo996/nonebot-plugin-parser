"""
测试不同的请求组合
"""

import asyncio
import httpx
import json


async def test_with_cookies():
    """测试带 cookies 的请求"""

    dynamic_id = 1156587796127809560

    url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
    params = {
        "id": dynamic_id,
        "timezone_offset": -480,
    }

    # 测试不同的 headers 组合
    test_cases = [
        ("无 headers", {}, {}),
        ("移动端 UA", {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        }, {}),
        ("桌面端 UA + Referer", {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }, {}),
    ]

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*20 + "测试不同请求组合" + " "*22 + "║")
    print("╚" + "="*68 + "╝")

    for name, headers, cookies in test_cases:
        print(f"\n{'='*70}")
        print(f"测试: {name}")
        print(f"{'='*70}")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params, headers=headers, cookies=cookies)

                print(f"状态码: {response.status_code}")

                if response.status_code == 200:
                    try:
                        data = response.json()
                        code = data.get('code')
                        message = data.get('message')

                        print(f"code: {code}")
                        print(f"message: {message}")

                        if code == 0:
                            print(f"✅ 成功！")
                            # 保存成功的数据
                            with open(f'success_{name.replace(" ", "_")}.json', 'w', encoding='utf-8') as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)
                            print(f"💾 数据已保存")

                        else:
                            print(f"❌ 失败")
                            # 显示完整响应用于调试
                            print(f"完整响应: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")

                    except Exception as e:
                        print(f"❌ JSON 解析失败: {e}")
                        print(f"响应内容: {response.text[:200]}")
                else:
                    print(f"响应内容: {response.text[:200]}")

        except Exception as e:
            print(f"❌ 请求失败: {e}")

        await asyncio.sleep(2)  # 避免频率限制


async def test_multiple_requests():
    """测试多次请求看是否稳定"""

    dynamic_id = 1156587796127809560

    url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
    params = {
        "id": dynamic_id,
        "timezone_offset": -480,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        "Referer": "https://m.bilibili.com/",
    }

    print(f"\n{'='*70}")
    print("测试连续请求稳定性")
    print(f"{'='*70}\n")

    success_count = 0
    fail_count = 0

    for i in range(5):
        print(f"第 {i+1} 次请求...")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params, headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    code = data.get('code')

                    if code == 0:
                        print(f"  ✅ 成功 (code: {code})")
                        success_count += 1
                    else:
                        print(f"  ❌ 失败 (code: {code}, message: {data.get('message')})")
                        fail_count += 1
                else:
                    print(f"  ❌ HTTP {response.status_code}")
                    fail_count += 1

        except Exception as e:
            print(f"  ❌ 异常: {e}")
            fail_count += 1

        await asyncio.sleep(3)  # 等待3秒

    print(f"\n统计:")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")


async def main():
    await test_with_cookies()
    await test_multiple_requests()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
