"""
测试 msgspec 转换修复（手动转换 orig 字段）
"""

import json
from pathlib import Path
from msgspec import convert

# 测试转换逻辑
def test_orig_conversion():
    """测试 orig 字段的单独转换"""

    print("="*70)
    print("测试 orig 字段的 msgspec 转换")
    print("="*70)

    # 读取原始数据
    input_dir = Path("tests/pipeline_output")
    with open(input_dir / "test2_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    # 测试完整的 DynamicData 转换
    print("\n1. 测试完整的 DynamicData 转换")
    print("-"*70)
    try:
        from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicData

        dynamic_data = convert(raw_data, DynamicData)
        print(f"   item.name: {dynamic_data.item.name}")
        print(f"   item.text: {dynamic_data.item.text}")
        print(f"   item.image_urls: {len(dynamic_data.item.image_urls)}")
        print(f"   orig: {dynamic_data.orig}")

        if dynamic_data.orig is None:
            print(f"   ⚠️  msgspec 未能转换嵌套的 orig 字段")
    except Exception as e:
        print(f"   ❌ 转换失败: {e}")
        import traceback
        traceback.print_exc()

    # 测试单独转换 orig 字段
    print("\n2. 测试单独转换 orig 字段")
    print("-"*70)
    try:
        from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicInfo

        orig_raw = raw_data.get('item', {}).get('orig')
        if orig_raw:
            orig_info = convert(orig_raw, DynamicInfo)
            print(f"   ✅ 转换成功")
            print(f"   name: {orig_info.name}")
            print(f"   text: {orig_info.text[:100] if orig_info.text else 'None'}...")
            print(f"   image_urls: {len(orig_info.image_urls)}")
            if orig_info.image_urls:
                for i, url in enumerate(orig_info.image_urls, 1):
                    print(f"     [{i}] {url}")
        else:
            print(f"   ⚠️  原数据中没有 orig 字段")
    except Exception as e:
        print(f"   ❌ 转换失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    test_orig_conversion()
