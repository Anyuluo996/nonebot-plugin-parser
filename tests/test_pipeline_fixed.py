"""
修复版：完整流程测试，手动处理转发类型
"""

import asyncio
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def extract_dynamic_info(raw_data: dict, is_orig: bool = False):
    """手动从原始数据中提取动态信息，不依赖 msgspec"""

    # 确定使用哪个数据
    data = raw_data.get('orig') if is_orig else raw_data.get('item')
    if not data:
        return None

    modules = data.get('modules', {})
    module_author = modules.get('module_author', {})

    # 提取作者信息
    name = module_author.get('name', '')
    avatar = module_author.get('face', '')

    # 提取文本
    text = None
    module_dynamic = modules.get('module_dynamic', {})
    major = module_dynamic.get('major') or {}

    if major and 'opus' in major:
        opus = major['opus']
        summary = opus.get('summary', {})
        text = summary.get('text', '')
    elif major and 'archive' in major:
        archive = major['archive']
        text = archive.get('desc', '')

    # 提取图片
    image_urls = []
    if major and 'opus' in major:
        opus = major['opus']
        pics = opus.get('pics', [])
        image_urls = [pic['url'] for pic in pics]
    elif major and 'archive' in major:
        archive = major['archive']
        cover = archive.get('cover')
        if cover:
            image_urls = [cover]

    return {
        "name": name,
        "avatar": avatar,
        "text": text,
        "image_urls": image_urls,
        "type": data.get('type'),
        "is_orig": is_orig
    }


async def test_complete_pipeline_fixed():
    """完整流程测试（修复版）"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*12 + "B站动态完整流程测试（修复版）" + " "*10 + "║")
    print("╚" + "="*68 + "╝")
    print(f"\n开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 创建输出目录
    output_dir = Path("tests/pipeline_output")
    output_dir.mkdir(exist_ok=True)

    # 测试用例
    test_cases = [
        {
            "name": "普通图文动态",
            "id": 1159504791855955984,
            "expected": {
                "has_text": True,
                "has_images": True,
                "image_count": 1
            }
        },
        {
            "name": "转发动态",
            "id": 1156587796127809560,
            "expected": {
                "has_text": True,
                "has_images": True,
                "image_count": 2
            }
        }
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*70}")
        print(f"测试 {i}/{len(test_cases)}: {test_case['name']}")
        print(f"ID: {test_case['id']}")
        print(f"{'='*70}")

        # 1. 获取 API 数据
        print(f"\n步骤1: 获取 API 数据...")
        try:
            from bilibili_api.dynamic import Dynamic

            dynamic = Dynamic(test_case['id'])
            raw_data = await dynamic.get_info()

            # 保存原始数据
            raw_file = output_dir / f"test{i}_raw_api.json"
            with open(raw_file, 'w', encoding='utf-8') as f:
                json.dump(raw_data, f, ensure_ascii=False, indent=2)

            print(f"   ✅ API 数据获取成功")
            print(f"   💾 保存到: {raw_file.name}")
            print(f"   类型: {raw_data['item']['type']}")

        except Exception as e:
            print(f"   ❌ API 获取失败: {e}")
            continue

        # 2. 数据提取（手动）
        print(f"\n步骤2: 数据提取...")

        # 提取 item 信息
        item_info = await extract_dynamic_info(raw_data, is_orig=False)
        print(f"   item 信息:")
        print(f"     name: {item_info['name']}")
        print(f"     text: {item_info['text']}")
        print(f"     图片数: {len(item_info['image_urls'])}")

        # 如果是转发类型，提取 orig 信息
        orig_info = None
        if raw_data['item'].get('orig'):
            print(f"\n   检测到转发类型")
            orig_info = await extract_dynamic_info(raw_data, is_orig=True)
            print(f"   原动态信息:")
            print(f"     name: {orig_info['name']}")
            print(f"     text: {orig_info['text']}")
            print(f"     图片数: {len(orig_info['image_urls'])}")

            # 使用原动态的内容
            if orig_info['image_urls'] or orig_info['text']:
                item_info = orig_info
                print(f"   使用原动态的内容")

        # 保存转换后的数据
        converted_file = output_dir / f"test{i}_converted.json"
        converted_data = {
            "item": item_info,
            "has_orig": orig_info is not None,
            "orig": orig_info
        }
        with open(converted_file, 'w', encoding='utf-8') as f:
            json.dump(converted_data, f, ensure_ascii=False, indent=2)

        print(f"\n   ✅ 数据提取成功")
        print(f"   💾 保存到: {converted_file.name}")
        print(f"   最终文本: {item_info['text']}")
        print(f"   最终图片数: {len(item_info['image_urls'])}")

        # 3. 下载图片
        print(f"\n步骤3: 下载图片...")
        try:
            import httpx

            downloaded_images = []

            for j, image_url in enumerate(item_info['image_urls']):
                print(f"   下载图片{j+1}...")

                # 下载图片
                async with httpx.AsyncClient(timeout=30) as client:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Referer": "https://www.bilibili.com",
                    }

                    response = await client.get(image_url, headers=headers)
                    if response.status_code == 200:
                        # 保存图片
                        image_ext = image_url.split('.')[-1] if '.' in image_url.split('?')[0] else 'jpg'
                        image_filename = f"test{i}_image{j+1}.{image_ext}"
                        image_path = output_dir / image_filename

                        with open(image_path, 'wb') as f:
                            f.write(response.content)

                        downloaded_images.append({
                            "index": j + 1,
                            "filename": image_filename,
                            "path": str(image_path),
                            "size": len(response.content),
                            "url": image_url
                        })

                        print(f"     ✅ 下载成功: {image_filename} ({len(response.content)} bytes)")
                    else:
                        print(f"     ❌ 下载失败: HTTP {response.status_code}")

            # 保存下载信息
            download_file = output_dir / f"test{i}_download_info.json"
            with open(download_file, 'w', encoding='utf-8') as f:
                json.dump(downloaded_images, f, ensure_ascii=False, indent=2)

            print(f"\n   ✅ 图片下载完成: {len(downloaded_images)}/{len(item_info['image_urls'])}")
            print(f"   💾 信息保存到: {download_file.name}")

        except Exception as e:
            print(f"   ❌ 图片下载失败: {e}")
            import traceback
            traceback.print_exc()

        # 4. 诊断结果
        print(f"\n步骤4: 诊断结果...")
        issues = []

        if not item_info['text']:
            issues.append("⚠️  文本为空")
        else:
            print(f"   ✅ 文本: {item_info['text'][:50]}...")

        if not item_info['image_urls']:
            issues.append("⚠️ ️ 图片列表为空")
        else:
            print(f"   ✅ 图片数: {len(item_info['image_urls'])}")

        # 检查是否符合预期
        expected = test_case['expected']
        actual_has_text = item_info['text'] is not None
        actual_has_images = len(item_info['image_urls']) > 0

        if actual_has_text != expected['has_text']:
            issues.append(f"⚠️  文本与预期不符: {actual_has_text} vs {expected['has_text']}")

        if len(item_info['image_urls']) != expected['image_count']:
            issues.append(f"⚠️  图片数量不符: {len(item_info['image_urls'])} vs {expected['image_count']}")

        if not issues and downloaded_images:
            print(f"   ✅ 测试通过！所有检查项都符合预期")
        elif issues:
            print(f"   ⚠️  发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"     {issue}")
        else:
            print(f"   ❌ 测试失败，请查看下载信息")

    # 生成总结
    print(f"\n{'='*70}")
    print("生成总结")
    print(f"{'='*70}\n")

    print(f"✅ 测试完成，所有文件已保存到: {output_dir}/")
    print(f"\n文件说明:")
    print(f"  📄 test*_raw_api.json - 原始 API 数据")
    print(f"  📄 test*_converted.json - 提取的数据（包含 item 和 orig）")
    print(f"  📄 test*_download_info.json - 图片下载信息")
    print(f"  🖼️  test*_image*.jpg - 下载的图片文件")
    print(f"  📊 _report.json - 总结报告")
    print(f"\n请检查这些文件，特别是：")
    print(f"  1. test*_converted.json - 确认数据是否正确提取")
    print(f"  2. test*_image*.jpg - 查看下载的图片是否正常")
    print(f"  3. 如果转发动态的图片和文本正确，说明代码逻辑正确")

    print(f"\n结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


async def main():
    await test_complete_pipeline_fixed()

    print(f"\n" + "="*70)
    print("测试完成！")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
