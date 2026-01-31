"""
测试 desc_text 属性和转发评论获取
"""

import json
from pathlib import Path
from msgspec import convert

def test_desc_text():
    """测试 desc_text 属性"""

    print("="*70)
    print("测试 desc_text 属性（转发评论）")
    print("="*70)

    sys.path.insert(0, 'src')
    from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicInfo, DynamicData

    # 测试转发动态
    print("\n1. 测试转发动态 (test2)")
    print("-"*70)

    input_dir = Path("tests/pipeline_output")
    with open(input_dir / "test2_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    # 转换 item
    item_info = convert(raw_data['item'], DynamicInfo)
    print(f"   item.name: {item_info.name}")
    print(f"   item.text (从 major): {item_info.text}")
    print(f"   item.desc_text (从 desc): {item_info.desc_text[:50] if item_info.desc_text else 'None'}...")
    print(f"   item.image_urls: {len(item_info.image_urls)}")

    # 转换 orig
    orig_info = convert(raw_data['item']['orig'], DynamicInfo)
    print(f"\n   orig.name: {orig_info.name}")
    print(f"   orig.text: {orig_info.text[:80] if orig_info.text else 'None'}...")
    print(f"   orig.desc_text: {orig_info.desc_text}")
    print(f"   orig.image_urls: {len(orig_info.image_urls)}")

    # 验证转发评论
    forward_comment = item_info.desc_text
    print(f"\n   ✅ 转发评论: {forward_comment}")

    # 测试普通动态
    print("\n2. 测试普通图文动态 (test1)")
    print("-"*70)

    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    item_info = convert(raw_data['item'], DynamicInfo)
    print(f"   item.name: {item_info.name}")
    print(f"   item.text: {item_info.text}")
    print(f"   item.desc_text: {item_info.desc_text}")
    print(f"   item.image_urls: {len(item_info.image_urls)}")

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


if __name__ == "__main__":
    import sys
    test_desc_text()
