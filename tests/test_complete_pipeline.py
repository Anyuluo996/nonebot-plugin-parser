"""
完整流程测试：从API获取到图片下载和渲染
所有输出保存到 tests/ 目录
"""

import asyncio
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_complete_pipeline():
    """完整流程测试"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*12 + "B站动态完整流程测试" + " "*14 + "║")
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

        except Exception as e:
            print(f"   ❌ API 获取失败: {e}")
            continue

        # 2. 数据转换
        print(f"\n步骤2: 数据转换...")
        try:
            from msgspec import convert

            # 直接导入 dynamic 模块
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "dynamic",
                "src/nonebot_plugin_parser/parsers/bilibili/dynamic.py"
            )
            dynamic_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dynamic_module)

            DynamicData = dynamic_module.DynamicData

            # 读取原始 JSON 数据并转换
            with open(output_dir / f"test{i}_raw_api.json", 'r', encoding='utf-8') as f:
                raw_data = json.load(f)

            dynamic_data = convert(raw_data, DynamicData)

            # 根据修复后的逻辑，如果是转发类型，使用 orig 的内容
            dynamic_info = dynamic_data.item
            if dynamic_data.orig:
                print(f"   检测到转发类型，使用原动态内容")
                dynamic_info = dynamic_data.orig

            # 保存转换后的数据
            converted_file = output_dir / f"test{i}_converted.json"
            converted_data = {
                "name": dynamic_info.name,
                "text": dynamic_info.text,
                "image_urls": dynamic_info.image_urls,
                "title": dynamic_info.title,
                "timestamp": dynamic_info.timestamp,
                "type": raw_data['item']['type'],
                "is_forward": dynamic_data.orig is not None,
                "orig_type": dynamic_data.orig.type if dynamic_data.orig else None
            }
            with open(converted_file, 'w', encoding='utf-8') as f:
                json.dump(converted_data, f, ensure_ascii=False, indent=2)

            print(f"   ✅ 数据转换成功")
            print(f"   💾 保存到: {converted_file.name}")
            print(f"   类型: {raw_data['item']['type']}")
            if dynamic_data.orig:
                print(f"   转发类型: {dynamic_data.orig.type}")
            print(f"   文本: {dynamic_info.text}")
            print(f"   图片数: {len(dynamic_info.image_urls)}")

        except Exception as e:
            print(f"   ❌ 数据转换失败: {e}")
            import traceback
            traceback.print_exc()
            continue

        # 3. 下载图片
        print(f"\n步骤3: 下载图片...")
        try:
            import httpx

            downloaded_images = []

            for j, image_url in enumerate(dynamic_info.image_urls):
                print(f"   下载图片{j+1}...")

                # 下载图片
                async with httpx.AsyncClient(timeout=30) as client:
                    # 使用适当的 headers
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

            print(f"   ✅ 图片下载完成: {len(downloaded_images)}/{len(dynamic_info.image_urls)}")
            print(f"   💾 信息保存到: {download_file.name}")

        except Exception as e:
            print(f"   ❌ 图片下载失败: {e}")
            import traceback
            traceback.print_exc()
            continue

        # 4. 尝试渲染（如果可能）
        print(f"\n步骤4: 检查渲染配置...")

        # 保存渲染配置信息
        render_info = {
            "test_case": test_case['name'],
            "id": test_case['id'],
            "data": {
                "has_text": dynamic_info.text is not None,
                "text": dynamic_info.text,
                "has_images": len(dynamic_info.image_urls) > 0,
                "image_count": len(dynamic_info.image_urls),
                "images": downloaded_images
            },
            "expected": test_case['expected'],
            "status": {
                "text_match": (dynamic_info.text is not None) == test_case['expected']['has_text'],
                "images_match": len(dynamic_info.image_urls) == test_case['expected']['image_count'],
                "all_match": None  # 稍后计算
            }
        }

        # 计算是否完全匹配
        render_info["status"]["all_match"] = (
            render_info["status"]["text_match"] and
            render_info["status"]["images_match"]
        )

        render_file = output_dir / f"test{i}_render_info.json"
        with open(render_file, 'w', encoding='utf-8') as f:
            json.dump(render_info, f, ensure_ascii=False, indent=2)

        print(f"   渲染配置: parser_render_type (见配置文件)")
        print(f"   💾 渲染信息保存到: {render_file.name}")

        # 5. 诊断结果
        print(f"\n步骤5: 诊断结果...")
        issues = []

        if not render_info["status"]["text_match"]:
            issues.append("⚠️  文本与预期不符")

        if not render_info["status"]["images_match"]:
            issues.append(f"⚠️  图片数量不符: {render_info['data']['image_count']} vs {test_case['expected']['image_count']}")

        if render_info["status"]["all_match"]:
            print(f"   ✅ 测试通过！")
        else:
            print(f"   ⚠️  测试有警告:")

        for issue in issues:
            print(f"     {issue}")

        if not downloaded_images:
            print(f"   ❌ 没有成功下载任何图片")

    # 6. 生成总结报告
    print(f"\n{'='*70}")
    print("生成总结报告")
    print(f"{'='*70}\n")

    report = {
        "test_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_tests": len(test_cases),
        "test_cases": [
            {
                "name": tc["name"],
                "id": tc["id"],
                "expected": tc["expected"]
            }
            for tc in test_cases
        ],
        "output_directory": str(output_dir),
        "files_generated": [str(f) for f in output_dir.glob("*.*")]
    }

    report_file = output_dir / "_report.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"✅ 报告已生成: tests/pipeline_output/_report.json")
    print(f"\n生成的文件位置: {output_dir}/")
    print(f"  - 原始 API 数据: test*_raw_api.json")
    print(f"  - 转换数据: test*_converted.json")
    print(f"  - 下载信息: test*_download_info.json")
    print(f"  - 渲染信息: test*_render_info.json")
    print(f"  - 图片文件: test*_image*.jpg/png")

    print(f"\n结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


async def test_image_render():
    """测试图片渲染（如果可能）"""
    print(f"\n{'='*70}")
    print("补充测试: 尝试实际渲染")
    print(f"{'='*70}\n")

    output_dir = Path("tests/pipeline_output")

    # 检查是否有 PIL
    try:
        from PIL import Image, ImageDraw, ImageFont

        print(f"✅ PIL 可用")

        # 创建一个简单的测试图片
        test_img = Image.new('RGB', (800, 400), color=(245, 245, 245))
        draw = ImageDraw.Draw(test_img)

        # 绘制边框
        draw.rectangle([10, 10, 790, 390], outline=(66, 135, 245), width=3)

        # 绘制文本
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except:
            font = ImageFont.load_default()

        draw.text((50, 50), "B站动态渲染测试", fill=(0, 0, 0), font=font)
        draw.text((50, 100), "时间: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'), fill=(100, 100, 100), font=font)

        # 保存测试图片
        test_img_path = output_dir / "_render_test.png"
        test_img.save(test_img_path)

        print(f"✅ 测试渲染图片已生成: {test_img_path.name}")
        print(f"   说明: 这是一个测试图片，用于验证渲染功能是否正常")

    except ImportError:
        print(f"⚠️  PIL 不可用，跳过图片渲染测试")
    except Exception as e:
        print(f"❌ 图片渲染测试失败: {e}")


async def main():
    await test_complete_pipeline()
    await test_image_render()

    print(f"\n{'='*70}")
    print("测试完成！")
    print(f"{'='*70}\n")
    print(f"请查看 tests/pipeline_output/ 目录下的文件：")
    print(f"  1. 检查 test*_converted.json - 确认数据转换正确")
    print(f"  2. 检查 test*_download_info.json - 确认图片下载成功")
    print(f"  3. 检查 test*_image*.jpg/png - 查看实际下载的图片")
    print(f"  4. 检查 test*_render_info.json - 查看渲染信息和诊断结果")
    print(f"\n如果有任何问题，请提供这些文件的内容以便调试。")


if __name__ == "__main__":
    asyncio.run(main())
