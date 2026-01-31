"""
使用 curl_cffi 测试B站URL
模拟浏览器TLS指纹，避免反爬检测
"""

import asyncio
import json
from curl_cffi import requests


def test_with_curl():
    """使用 curl_cffi 测试各个URL"""

    opus_id = 1156587796127809560

    urls = [
        ("桌面版 opus", f"https://www.bilibili.com/opus/{opus_id}"),
        ("移动版 opus", f"https://m.bilibili.com/opus/{opus_id}"),
        ("桌面版 dynamic", f"https://www.bilibili.com/dynamic/{opus_id}"),
        ("移动版 dynamic", f"https://m.bilibili.com/dynamic/{opus_id}"),
    ]

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*18 + "使用 curl_cffi 测试B站URL" + " "*18 + "║")
    print("╚" + "="*68 + "╝")

    for name, url in urls:
        print(f"\n{'='*70}")
        print(f"测试: {name}")
        print(f"{'='*70}")
        print(f"URL: {url}\n")

        try:
            # 使用 Chrome 浏览器指纹
            response = requests.get(
                url,
                impersonate="chrome",  # 模拟 Chrome 浏览器
                timeout=30,
                allow_redirects=True,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "keep-alive",
                }
            )

            print(f"✅ 请求成功")
            print(f"   状态码: {response.status_code}")
            print(f"   最终 URL: {response.url}")
            print(f"   内容长度: {len(response.content)}")
            print(f"   Content-Type: {response.headers.get('Content-Type')}")

            # 检查是否包含数据
            text = response.text
            has_initial_state = "__INITIAL_STATE__" in text
            has_opus_data = '"opus"' in text or "'opus'" in text

            print(f"\n   数据检测:")
            print(f"   __INITIAL_STATE__: {'✅' if has_initial_state else '❌'}")
            print(f"   opus 数据: {'✅' if has_opus_data else '❌'}")

            # 显示前500字符
            print(f"\n   内容预览:")
            preview = text[:500].replace('\n', '\\n')
            print(f"   {preview}...")

            # 如果有 __INITIAL_STATE__，尝试提取
            if has_initial_state:
                import re
                pattern = r'__INITIAL_STATE__\s*=\s*({.+?});'
                match = re.search(pattern, text)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        print(f"\n   ✅ 成功解析 __INITIAL_STATE__")
                        print(f"   顶层键: {list(data.keys())[:10]}")

                        # 保存到文件
                        filename = f"curl_{name.replace(' ', '_')}_data.json"
                        with open(filename, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        print(f"   💾 数据已保存: {filename}")

                    except Exception as e:
                        print(f"\n   ❌ 解析失败: {e}")

        except Exception as e:
            print(f"❌ 请求失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

        print()


def test_opus_api():
    """测试 opus API 接口"""
    opus_id = 1156587796127809560

    print(f"\n{'='*70}")
    print(f"测试 Opus API 接口")
    print(f"{'='*70}\n")

    url = "https://api.bilibili.com/x/dynamic/feed/dynamic_detail"
    params = {"dynamic_id": opus_id}

    try:
        response = requests.get(
            url,
            params=params,
            impersonate="chrome",
            timeout=30,
            headers={
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com",
            }
        )

        print(f"状态码: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")

        if response.status_code == 200:
            try:
                data = response.json()
                print(f"\n✅ JSON 解析成功")
                print(f"   code: {data.get('code')}")
                print(f"   message: {data.get('message')}")

                if data.get('code') == 0:
                    print(f"   ✅ API 请求成功")

                    # 保存完整响应
                    with open('curl_opus_api_response.json', 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"   💾 数据已保存: curl_opus_api_response.json")
                else:
                    print(f"   ❌ API 返回错误")

                print(f"\n   数据预览:")
                print(f"   {json.dumps(data, ensure_ascii=False, indent=2)[:1000]}...")

            except Exception as e:
                print(f"❌ JSON 解析失败: {e}")
                print(f"   响应内容: {response.text[:500]}")
        else:
            print(f"   响应内容: {response.text[:500]}")

    except Exception as e:
        print(f"❌ 请求失败: {type(e).__name__}: {e}")


def main():
    test_with_curl()
    test_opus_api()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
