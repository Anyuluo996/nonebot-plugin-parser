"""
手动解析测试（不依赖 msgspec）
模拟 parse_dynamic 的完整逻辑
"""

import json
from pathlib import Path


def extract_text_from_major(major: dict) -> str | None:
    """从 major 中提取文本"""
    if not major:
        return None

    if 'opus' in major:
        opus = major['opus']
        summary = opus.get('summary', {})
        return summary.get('text')
    elif 'archive' in major:
        archive = major['archive']
        return archive.get('desc')
    return None


def extract_images_from_major(major: dict) -> list[str]:
    """从 major 中提取图片URL列表"""
    if not major:
        return []

    if 'opus' in major:
        opus = major['opus']
        pics = opus.get('pics', [])
        return [pic['url'] for pic in pics]
    elif 'archive' in major:
        archive = major['archive']
        cover = archive.get('cover')
        return [cover] if cover else []
    return []


def extract_desc_text(modules: dict) -> str | None:
    """从 modules.module_dynamic.desc 中提取转发评论"""
    module_dynamic = modules.get('module_dynamic', {})
    if not module_dynamic:
        return None

    desc = module_dynamic.get('desc')
    if desc:
        return desc.get('text')
    return None


def simulate_parse_dynamic(raw_data: dict):
    """模拟 parse_dynamic 的完整逻辑"""

    print("  模拟 parse_dynamic 流程:")

    item = raw_data.get('item', {})
    modules = item.get('modules', {})
    module_author = modules.get('module_author', {})

    # 提取基本信息
    name = module_author.get('name', '')
    avatar = module_author.get('face', '')
    item_type = item.get('type', '')

    print(f"    item.type: {item_type}")
    print(f"    item.name: {name}")

    # 检查是否转发
    orig = item.get('orig')

    if orig:
        print(f"\n    ✅ 检测到转发类型")
        orig_modules = orig.get('modules', {})
        orig_module_author = orig_modules.get('module_author', {})

        orig_name = orig_module_author.get('name', '')
        orig_avatar = orig_module_author.get('face', '')
        orig_module_dynamic = orig_modules.get('module_dynamic', {})
        orig_major = orig_module_dynamic.get('major')

        # 提取原动态内容
        orig_text = extract_text_from_major(orig_major)
        orig_images = extract_images_from_major(orig_major)

        # 提取转发评论
        forward_comment = extract_desc_text(modules)

        print(f"\n    原动态信息:")
        print(f"      作者: {orig_name}")
        print(f"      文本: {orig_text[:80] if orig_text else 'None'}...")
        print(f"      图片数: {len(orig_images)}")

        print(f"\n    转发评论:")
        print(f"      {forward_comment[:50] if forward_comment else 'None'}...")

        # 使用原动态内容
        final_name = orig_name
        final_avatar = orig_avatar
        final_text = orig_text
        final_images = orig_images

        # 添加转发评论
        if forward_comment:
            if final_text:
                final_text = f"{forward_comment}\n\n---\n\n{final_text}"
            else:
                final_text = forward_comment

    else:
        print(f"\n    ℹ️  非转发类型")

        # 直接从 item 提取
        module_dynamic = modules.get('module_dynamic', {})
        major = module_dynamic.get('major')

        final_text = extract_text_from_major(major)
        final_images = extract_images_from_major(major)
        final_name = name
        final_avatar = avatar

    print(f"\n    最终结果:")
    print(f"      作者: {final_name}")
    print(f"      文本: {final_text[:80] if final_text else 'None'}...")
    print(f"      图片数: {len(final_images)}")

    if final_images:
        print(f"      图片列表:")
        for i, url in enumerate(final_images, 1):
            print(f"        [{i}] {url}")

    return {
        'name': final_name,
        'text': final_text,
        'images': final_images,
        'is_forward': orig is not None
    }


def main():
    print("="*70)
    print("模拟 parse_dynamic 完整逻辑测试")
    print("="*70)

    input_dir = Path("tests/pipeline_output")

    # 测试1: 普通动态
    print("\n测试1: 普通图文动态 (1159504791855955984)")
    print("-"*70)

    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    result1 = simulate_parse_dynamic(raw_data)

    print(f"\n  诊断:")
    if not result1['text']:
        print(f"    ⚠️  文本为空")
    else:
        print(f"    ✅ 文本正常")

    if len(result1['images']) == 0:
        print(f"    ❌ 图片列表为空 - 无法渲染图片内容！")
    else:
        print(f"    ✅ 图片列表正常 - 可以渲染 {len(result1['images'])} 张图片")

    # 测试2: 转发动态
    print("\n" + "="*70)
    print("测试2: 转发动态 (1156587796127809560)")
    print("-"*70)

    with open(input_dir / "test2_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    result2 = simulate_parse_dynamic(raw_data)

    print(f"\n  诊断:")
    if not result2['text']:
        print(f"    ⚠️  文本为空")
    else:
        print(f"    ✅ 文本正常（包含转发评论）")

    if len(result2['images']) == 0:
        print(f"    ❌ 图片列表为空 - 无法渲染图片内容！")
    else:
        print(f"    ✅ 图片列表正常 - 可以渲染 {len(result2['images'])} 张图片")

    # 总结
    print("\n" + "="*70)
    print("测试总结")
    print("="*70)

    issues = []

    if len(result1['images']) == 0:
        issues.append("普通动态图片列表为空")
    if len(result2['images']) == 0:
        issues.append("转发动态图片列表为空")
    if not result2['text']:
        issues.append("转发动态文本为空")

    if issues:
        print(f"\n❌ 发现问题:")
        for issue in issues:
            print(f"  - {issue}")
        print(f"\n  需要修复代码！")
    else:
        print(f"\n✅ 所有测试通过！")
        print(f"\n  普通动态: 文本 ✅, 图片 ✅ ({len(result1['images'])} 张)")
        print(f"  转发动态: 文本 ✅, 图片 ✅ ({len(result2['images'])} 张)")
        print(f"\n  代码逻辑正确，可以正常渲染！")


if __name__ == "__main__":
    main()
