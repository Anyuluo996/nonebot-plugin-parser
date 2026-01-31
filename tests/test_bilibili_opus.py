"""
测试B站opus API接口
测试链接: https://www.bilibili.com/opus/1156587796127809560
"""

import asyncio
import json


async def test_opus_web_api():
    """测试B站opus网页API"""
    import httpx

    opus_id = 1156587796127809560

    print(f"\n{'='*70}")
    print(f"测试 Opus Web API: {opus_id}")
    print(f"{'='*70}\n")

    # B站 opus API
    url = f"https://www.bilibili.com/opus/{opus_id}"

    print(f"1. 请求 URL: {url}")
    print(f"{'-'*70}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            print(f"✅ 成功获取响应")
            print(f"   状态码: {response.status_code}")
            print(f"   最终 URL: {str(response.url)}")
            print(f"   内容长度: {len(response.content)}")

            # 检查是否包含页面数据
            text = response.text
            if "__INITIAL_STATE__" in text:
                print(f"   包含 __INITIAL_STATE__: ✅")
                # 提取数据
                start = text.index("__INITIAL_STATE__") + len("__INITIAL_STATE__") + 1
                end = text.index("};", start) + 1
                data_str = text[start:end]
                try:
                    data = json.loads(data_str)
                    print(f"   数据结构: {list(data.keys())[:10]}")
                except:
                    print(f"   解析 JSON 失败")
            else:
                print(f"   包含 __INITIAL_STATE__: ❌")

            # 检查是否包含错误信息
            if "稿件不存在" in text or "404" in text or "访问异常" in text:
                print(f"   ⚠️  页面显示错误")

            # 显示前500字符
            print(f"\n   内容预览:")
            print(f"   {text[:500]}...")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-"*70 + "\n")


async def test_dynamic_web_api():
    """测试B站dynamic网页API"""
    import httpx

    dynamic_id = 1156587796127809560

    print(f"\n{'='*70}")
    print(f"测试 Dynamic Web API: {dynamic_id}")
    print(f"{'='*70}\n")

    # B站 dynamic API
    url = f"https://m.bilibili.com/dynamic/{dynamic_id}"

    print(f"1. 请求 URL: {url}")
    print(f"{'-'*70}")

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            print(f"✅ 成功获取响应")
            print(f"   状态码: {response.status_code}")
            print(f"   最终 URL: {str(response.url)}")
            print(f"   内容长度: {len(response.content)}")

            # 检查是否包含页面数据
            text = response.text
            if "__INITIAL_STATE__" in text:
                print(f"   包含 __INITIAL_STATE__: ✅")
                # 提取数据
                start = text.index("__INITIAL_STATE__") + len("__INITIAL_STATE__") + 1
                end = text.index("};", start) + 1
                data_str = text[start:end]
                try:
                    data = json.loads(data_str)
                    print(f"   数据结构: {list(data.keys())[:10]}")
                except:
                    print(f"   解析 JSON 失败")
            else:
                print(f"   包含 __INITIAL_STATE__: ❌")

            # 检查是否包含错误信息
            if "稿件不存在" in text or "404" in text or "访问异常" in text:
                print(f"   ⚠️  页面显示错误")

            # 显示前500字符
            print(f"\n   内容预览:")
            print(f"   {text[:500]}...")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-"*70 + "\n")


async def test_opus_api():
    """测试B站opus接口"""
    import httpx

    opus_id = 1156587796127809560

    print(f"\n{'='*70}")
    print(f"测试 Opus API 接口: {opus_id}")
    print(f"{'='*70}\n")

    # B站 opus API endpoint
    url = "https://api.bilibili.com/x/dynamic/feed/dynamic_detail"
    params = {"dynamic_id": opus_id}

    print(f"1. 请求 API: {url}")
    print(f"   参数: {params}")
    print(f"{'-'*70}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=headers)
            print(f"✅ 成功获取响应")
            print(f"   状态码: {response.status_code}")

            data = response.json()
            print(f"   code: {data.get('code')}")
            print(f"   message: {data.get('message')}")

            if data.get("code") == 0:
                print(f"   请求成功: ✅")
                item = data.get("data", {}).get("item", {})
                print(f"   item 类型: {item.get('type')}")
                print(f"   数据预览: {json.dumps(data, ensure_ascii=False, indent=2)[:1000]}...")
            else:
                print(f"   请求失败: ❌")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-"*70 + "\n")


async def main():
    """主测试函数"""
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*18 + "B站 Opus Web API 测试" + " "*22 + "║")
    print("╚" + "="*68 + "╝")

    # 测试 opus 网页API
    await test_opus_web_api()

    # 测试 dynamic 网页API
    await test_dynamic_web_api()

    # 测试 opus API接口
    await test_opus_api()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
