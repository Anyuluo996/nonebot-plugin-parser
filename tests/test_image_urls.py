"""
测试 msgspec 转换后的 image_urls 属性
"""

import json
from pathlib import Path

def test_image_urls():
    """测试 image_urls 属性"""

    print("="*70)
    print("测试 msgspec 转换后的 image_urls")
    print("="*70)

    sys.path.insert(0, 'src')
    from msgspec import convert
    from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicInfo

    input_dir = Path("tests/pipeline_output")

    # 测试普通动态
    print("\n1. 测试普通图文动态 (test1)")
    print("-"*70)

    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    item_raw = raw_data['item']
    print(f"   原始 major: {item_raw['modules']['module_dynamic'].get('major') is not None}")
    if item_raw['modules']['module_dynamic'].get('major'):
        print(f"   原始 major.type: {item_raw['modules']['module_dynamic']['major']['type']}")
        print(f"   原始 major.opus.pics: {len(item_raw['modules']['module_dynamic']['major']['opus']['pics'])}")

    # 转换
    item_info = convert(item_raw, DynamicInfo)

    print(f"\n   转换后:")
    print(f"   item.name: {item_info.name}")
    print(f"   item.text: {item_info.text}")
    print(f"   item.image_urls: {item_info.image_urls}")
    print(f"   item.image_urls 长度: {len(item_info.image_urls)}")

    if len(item_info.image_urls) == 0:
        print(f"   ❌ 图片列表为空！")
    else:
        print(f"   ✅ 图片列表正常")

    # 测试转发动态
    print("\n2. 测试转发动态 (test2)")
    print("-"*70)

    with open(input_dir / "test2_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    # item (转发者)
    item_raw = raw_data['item']
    print(f"   item (转发者):")
    print(f"   原始 major: {item_raw['modules']['module_dynamic'].get('major')}")

    item_info = convert(item_raw, DynamicInfo)
    print(f"   转换后 item.image_urls: {len(item_info.image_urls)}")

    # orig (原动态)
    orig_raw = raw_data['item']['orig']
    print(f"\n   orig (原动态):")
    print(f"   原始 major: {orig_raw['modules']['module_dynamic'].get('major') is not None}")
    if orig_raw['modules']['module_dynamic'].get('major'):
        print(f"   原始 major.type: {orig_raw['modules']['module_dynamic']['major']['type']}")
        print(f"   原始 major.opus.pics: {len(orig_raw['modules']['module_dynamic']['major']['opus']['pics'])}")

    orig_info = convert(orig_raw, DynamicInfo)
    print(f"\n   转换后 orig.image_urls: {len(orig_info.image_urls)}")
    print(f"   orig.image_urls: {orig_info.image_urls}")

    if len(orig_info.image_urls) == 0:
        print(f"   ❌ 图片列表为空！")
    else:
        print(f"   ✅ 图片列表正常")

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


if __name__ == "__main__":
    import sys
    test_image_urls()
