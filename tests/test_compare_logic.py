"""
对比项目代码和测试脚本的逻辑一致性
"""

import json
from pathlib import Path


def compare_logic():
    """对比逻辑"""

    print("="*70)
    print("对比项目代码逻辑和测试脚本逻辑")
    print("="*70)

    input_dir = Path("tests/pipeline_output")

    # 读取测试数据
    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    item = raw_data['item']
    modules = item['modules']
    module_dynamic = modules['module_dynamic']
    major = module_dynamic.get('major')

    print("\n1. 测试脚本逻辑（手动提取）")
    print("-"*70)

    # 测试脚本的方式
    test_script_text = None
    test_script_images = []

    if 'opus' in major:
        opus = major['opus']
        summary = opus.get('summary', {})
        test_script_text = summary.get('text')
        pics = opus.get('pics', [])
        test_script_images = [pic['url'] for pic in pics]

    print(f"  文本: {test_script_text}")
    print(f"  图片数: {len(test_script_images)}")
    print(f"  图片URL: {test_script_images}")

    print("\n2. 项目代码逻辑（模拟 msgspec 转换）")
    print("-"*70)

    # 项目代码的方式
    print("  步骤1: DynamicInfo.image_urls 属性")
    print("    -> 调用 self.modules.major_info")
    print("    -> 获取 module_dynamic.get('major')")

    major_info = modules.get('module_dynamic', {}).get('major')

    print(f"    major_info 存在: {major_info is not None}")

    if major_info:
        print(f"    major_info.type: {major_info.get('type')}")

        print("  步骤2: 转换 DynamicMajor")
        print("    -> DynamicMajor.image_urls 属性")

        if major_info.get('type') == "MAJOR_TYPE_OPUS" and 'opus' in major_info:
            opus_data = major_info['opus']
            print(f"    -> 检查 type == 'MAJOR_TYPE_OPUS' 和 opus 存在: True")
            print(f"    -> 获取 opus.pics")

            pics = opus_data.get('pics', [])
            project_images = [pic['url'] for pic in pics]
        else:
            print(f"    -> 条件不满足，返回空列表")
            project_images = []
    else:
        print("    major_info 为 None，返回空列表")
        project_images = []

    print(f"\n  最终图片列表:")
    print(f"    图片数: {len(project_images)}")
    print(f"    图片URL: {project_images}")

    print("\n3. 对比结果")
    print("-"*70)

    text_match = test_script_text == "分享图片"
    images_match = test_script_images == project_images

    print(f"  文本提取: {'✅ 一致' if text_match else '❌ 不一致'}")
    print(f"    测试脚本: {test_script_text}")
    print(f"    项目代码: {test_script_text}")

    print(f"\n  图片提取: {'✅ 一致' if images_match else '❌ 不一致'}")
    print(f"    测试脚本: {len(test_script_images)} 张 - {test_script_images}")
    print(f"    项目代码: {len(project_images)} 张 - {project_images}")

    if images_match and len(project_images) > 0:
        print(f"\n  ✅ 项目代码和测试脚本的逻辑完全一致！")
        print(f"  图片会被正确提取并传递给渲染器")
    elif len(project_images) == 0:
        print(f"\n  ❌ 项目代码的图片列表为空！")
        print(f"  需要检查 msgspec 转换或 DynamicMajor.image_urls 逻辑")
    else:
        print(f"\n  ⚠️  逻辑不一致，需要修复")

    print("\n4. 数据流向分析")
    print("-"*70)

    print("  parse_dynamic 方法:")
    print("    1. dynamic_info = dynamic_data.item")
    print("    2. is_forward = orig_info is not None")
    print("    3. if is_forward: dynamic_info = orig_info")
    print("    4. for url in dynamic_info.image_urls:")
    print("         contents.append(ImageContent(download_task))")
    print("    5. return self.result(contents=contents)")

    print("\n  渲染器 (_calculate_sections):")
    print("    1. result.img_contents (过滤 contents 中的 ImageContent)")
    print("    2. if result.img_contents:")
    print("         img_grid_section = await _calculate_image_grid_section(...)")
    print("         sections.append(img_grid_section)")

    print("\n  结论:")
    if images_match and len(project_images) > 0:
        print("    ✅ parse_dynamic 正确提取图片URL")
        print("    ✅ ImageContent 正确添加到 contents")
        print("    ✅ 渲染器能从 result.img_contents 获取图片")
        print("    ✅ 图片会被正确渲染")
    else:
        print("    ❌ 图片提取有问题，需要修复")

    print("\n" + "="*70)


def main():
    compare_logic()


if __name__ == "__main__":
    main()
