"""
手动解析 B站动态 JSON 数据（不依赖 msgspec）
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


def extract_text_from_desc(modules: dict) -> str | None:
    """从 modules.module_dynamic.desc 中提取文本（用于转发评论）"""
    module_dynamic = modules.get('module_dynamic', {})
    if not module_dynamic:
        return None

    desc = module_dynamic.get('desc')
    if desc:
        return desc.get('text')
    return None


def parse_dynamic_from_raw(raw_data: dict):
    """从原始 API 数据解析动态信息"""
    item = raw_data.get('item', {})
    modules = item.get('modules', {})
    module_author = modules.get('module_author', {})

    # 提取作者信息
    name = module_author.get('name', '')
    avatar = module_author.get('face', '')
    timestamp = module_author.get('pub_ts', 0)

    # 检查是否是转发类型
    orig = item.get('orig')

    if orig:
        print(f"✅ 检测到转发类型动态")
        print(f"   转发者: {name}")

        # 从 orig 中提取实际内容
        orig_modules = orig.get('modules', {})
        orig_module_dynamic = orig_modules.get('module_dynamic', {})
        orig_major = orig_module_dynamic.get('major')

        # 原作者信息
        orig_module_author = orig_modules.get('module_author', {})
        orig_name = orig_module_author.get('name', '')
        orig_avatar = orig_module_author.get('face', '')

        # 提取原动态的文本和图片
        text = extract_text_from_major(orig_major)
        image_urls = extract_images_from_major(orig_major)

        # 提取转发评论
        forward_comment = extract_text_from_desc(modules)

        print(f"   原作者: {orig_name}")
        print(f"   原动态文本: {text[:50] if text else 'None'}...")
        print(f"   原动态图片数: {len(image_urls)}")
        print(f"   转发评论: {forward_comment[:30] if forward_comment else 'None'}...")

        # 如果原动态有内容，使用原动态
        if text or image_urls:
            return {
                'name': orig_name,
                'avatar': orig_avatar,
                'text': text,
                'image_urls': image_urls,
                'timestamp': timestamp,
                'forward_comment': forward_comment,
                'forwarder': name,
                'is_forward': True
            }

    # 非转发类型，直接从 item 提取
    module_dynamic = modules.get('module_dynamic', {})
    major = module_dynamic.get('major')

    text = extract_text_from_major(major)
    image_urls = extract_images_from_major(major)

    print(f"   作者: {name}")
    print(f"   文本: {text[:50] if text else 'None'}...")
    print(f"   图片数: {len(image_urls)}")

    return {
        'name': name,
        'avatar': avatar,
        'text': text,
        'image_urls': image_urls,
        'timestamp': timestamp,
        'is_forward': False
    }


def main():
    print("="*70)
    print("手动解析 B站动态 JSON")
    print("="*70)

    input_dir = Path("tests/pipeline_output")

    # 测试转发动态
    print("\n测试1: 转发动态")
    print("-"*70)
    with open(input_dir / "test2_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    result = parse_dynamic_from_raw(raw_data)
    print(f"\n解析结果:")
    print(f"  作者: {result['name']}")
    print(f"  文本: {result['text'][:100] if result['text'] else 'None'}...")
    print(f"  图片数: {len(result['image_urls'])}")
    if result['image_urls']:
        for i, url in enumerate(result['image_urls'], 1):
            print(f"    [{i}] {url}")
    if result.get('forward_comment'):
        print(f"  转发评论: {result['forward_comment']}")
        print(f"  转发者: {result['forwarder']}")

    # 保存解析结果
    output_file = input_dir / "test2_manual_parse.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 解析结果保存到: {output_file.name}")

    # 测试普通动态
    print("\n" + "="*70)
    print("测试2: 普通图文动态")
    print("-"*70)
    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    result = parse_dynamic_from_raw(raw_data)
    print(f"\n解析结果:")
    print(f"  作者: {result['name']}")
    print(f"  文本: {result['text'][:100] if result['text'] else 'None'}...")
    print(f"  图片数: {len(result['image_urls'])}")
    if result['image_urls']:
        for i, url in enumerate(result['image_urls'], 1):
            print(f"    [{i}] {url}")

    # 保存解析结果
    output_file = input_dir / "test1_manual_parse.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 解析结果保存到: {output_file.name}")

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


if __name__ == "__main__":
    main()
