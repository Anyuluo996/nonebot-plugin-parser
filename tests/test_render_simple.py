"""
简化版渲染测试 - 直接测试关键数据
"""

import asyncio
import json
from pathlib import Path


async def test_render_data():
    """测试渲染数据是否正确"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "B站动态渲染数据检查" + " "*17 + "║")
    print("╚" + "="*68 + "╝")

    # 加载已保存的 API 数据
    with open('tests/render_raw_api_data.json', 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    print(f"\n1. 原始 API 数据检查:")
    print(f"   item.type: {raw_data['item']['type']}")

    # 检查 modules
    modules = raw_data['item'].get('modules', {})
    print(f"   modules 键: {list(modules.keys())}")

    # 检查 module_dynamic
    module_dynamic = modules.get('module_dynamic', {})
    print(f"\n2. module_dynamic 检查:")
    print(f"   键: {list(module_dynamic.keys())}")

    # 检查 major
    major = module_dynamic.get('major')
    if major:
        print(f"\n3. major 检查:")
        print(f"   类型: {major.get('type')}")
        print(f"   键: {list(major.keys())}")

        # 检查 opus
        if 'opus' in major:
            opus = major['opus']
            print(f"\n4. opus 检查:")
            print(f"   键: {list(opus.keys())}")

            # 检查图片
            pics = opus.get('pics', [])
            print(f"   图片数量: {len(pics)}")

            if pics:
                print(f"\n5. 图片详情:")
                for i, pic in enumerate(pics):
                    print(f"   图片{i+1}:")
                    print(f"     url: {pic.get('url')}")
                    print(f"     width: {pic.get('width')}")
                    print(f"     height: {pic.get('height')}")
                    print(f"     size: {pic.get('size')}")

            # 检查 summary
            summary = opus.get('summary', {})
            text = summary.get('text', '')
            print(f"\n6. 文本内容:")
            print(f"   text: {text}")

    # 检查 orig（转发）
    orig = raw_data['item'].get('orig')
    print(f"\n7. 转发检查:")
    if orig:
        print(f"   有 orig: ✅ (转发类型)")
        print(f"   orig.type: {orig.get('type')}")

        # 检查 orig 的内容
        orig_modules = orig.get('modules', {})
        orig_dynamic = orig_modules.get('module_dynamic', {})
        orig_major = orig_dynamic.get('major', {})

        if 'opus' in orig_major:
            orig_opus = orig_major['opus']
            orig_pics = orig_opus.get('pics', [])
            print(f"   原动态图片数: {len(orig_pics)}")
    else:
        print(f"   有 orig: ❌ (非转发类型)")

    # 模拟 msgspec 转换
    print(f"\n{'='*70}")
    print("8. 模拟 msgspec 转换")
    print(f"{'='*70}\n")

    from msgspec import convert

    # 直接导入 dynamic 模块
    import importlib.util
    spec = importlib.util.spec_from_file_location("dynamic", "src/nonebot_plugin_parser/parsers/bilibili/dynamic.py")
    dynamic_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dynamic_module)

    DynamicData = dynamic_module.DynamicData

    try:
        dynamic_data = convert(raw_data, DynamicData)
        print(f"   ✅ msgspec 转换成功")

        # 检查转换后的数据
        item_info = dynamic_data.item
        print(f"\n9. 转换后的数据:")
        print(f"   name: {item_info.name}")
        print(f"   text: {item_info.text}")
        print(f"   image_urls: {item_info.image_urls}")

        # 保存转换后的数据
        output_dir = Path("tests/render_output")
        output_dir.mkdir(exist_ok=True)

        converted_data = {
            "item_info": {
                "name": item_info.name,
                "text": item_info.text,
                "image_urls": item_info.image_urls,
                "title": item_info.title,
                "timestamp": item_info.timestamp,
            }
        }

        with open(output_dir / 'converted_data.json', 'w', encoding='utf-8') as f:
            json.dump(converted_data, f, ensure_ascii=False, indent=2)

        print(f"\n   💾 转换数据已保存: {output_dir / 'converted_data.json'}")

        # 诊断问题
        print(f"\n{'='*70}")
        print("10. 问题诊断")
        print(f"{'='*70}\n")

        issues = []

        if not item_info.text:
            issues.append("❌ text 为空 - 不会发送文字")
        else:
            print(f"   ✅ text 不为空: {item_info.text}")

        if not item_info.image_urls:
            issues.append("❌ image_urls 为空 - 不会发送图片")
        else:
            print(f"   ✅ image_urls 不为空: {len(item_info.image_urls)} 张")

        if issues:
            print(f"\n   发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"     {issue}")
        else:
            print(f"\n   ✅ 数据完整，应该可以正常渲染")

    except Exception as e:
        print(f"   ❌ msgspec 转换失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*70}")
    print("生成的文件:")
    print(f"{'='*70}")
    print(f"   1. tests/render_raw_api_data.json - 原始 API 数据")
    print(f"   2. tests/render_output/converted_data.json - 转换后的数据")

    print(f"\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


async def main():
    await test_render_data()


if __name__ == "__main__":
    asyncio.run(main())
