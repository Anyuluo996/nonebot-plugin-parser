"""
详细测试 msgspec 转换和图片获取流程
"""

import json
from pathlib import Path

def test_conversion_flow():
    """测试完整的转换流程"""

    print("="*70)
    print("测试 msgspec 转换和图片获取流程")
    print("="*70)

    sys.path.insert(0, 'src')
    from msgspec import convert
    from nonebot_plugin_parser.parsers.bilibili.dynamic import (
        DynamicData, DynamicInfo, DynamicModule, DynamicMajor
    )

    input_dir = Path("tests/pipeline_output")

    # 测试普通动态
    print("\n1. 测试普通图文动态 (test1) - 逐步转换")
    print("-"*70)

    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    item_raw = raw_data['item']

    # 步骤1: 检查原始数据
    print(f"   步骤1: 检查原始数据")
    print(f"     item.modules 存在: {item_raw.get('modules') is not None}")
    print(f"     item.modules.module_dynamic 存在: {item_raw['modules'].get('module_dynamic') is not None}")

    module_dynamic_raw = item_raw['modules']['module_dynamic']
    print(f"     module_dynamic.major 存在: {module_dynamic_raw.get('major') is not None}")

    if module_dynamic_raw.get('major'):
        major_raw = module_dynamic_raw['major']
        print(f"     major.type: {major_raw.get('type')}")
        print(f"     major.opus 存在: {'opus' in major_raw}")
        if 'opus' in major_raw:
            print(f"     major.opus.pics 数量: {len(major_raw['opus'].get('pics', []))}")

    # 步骤2: 转换 DynamicInfo
    print(f"\n   步骤2: 转换 DynamicInfo")
    item_info = convert(item_raw, DynamicInfo)
    print(f"     item.name: {item_info.name}")
    print(f"     item.modules 类型: {type(item_info.modules)}")

    # 步骤3: 检查 modules
    print(f"\n   步骤3: 检查 modules.module_dynamic")
    module_dynamic = item_info.modules.module_dynamic
    print(f"     module_dynamic 类型: {type(module_dynamic)}")
    print(f"     module_dynamic 为 None: {module_dynamic is None}")

    if module_dynamic:
        print(f"     module_dynamic.major 类型: {type(module_dynamic.get('major'))}")
        major_info = module_dynamic.get('major')
        print(f"     major_info 为 None: {major_info is None}")

        if major_info:
            print(f"\n   步骤4: 转换 DynamicMajor")
            major = convert(major_info, DynamicMajor)
            print(f"     major.type: {major.type}")
            print(f"     major.opus 为 None: {major.opus is None}")

            if major.opus:
                print(f"     major.opus.pics 数量: {len(major.opus.pics)}")

            print(f"\n   步骤5: 获取 image_urls")
            image_urls = major.image_urls
            print(f"     image_urls 数量: {len(image_urls)}")
            print(f"     image_urls: {image_urls}")
        else:
            print(f"     ❌ major_info 为 None！")
    else:
        print(f"     ❌ module_dynamic 为 None！")

    print(f"\n   步骤6: 使用 DynamicInfo.image_urls 属性")
    image_urls_property = item_info.image_urls
    print(f"     image_urls 属性数量: {len(image_urls_property)}")
    print(f"     image_urls 属性: {image_urls_property}")

    if len(image_urls_property) == 0:
        print(f"     ❌ 图片列表为空！")
    else:
        print(f"     ✅ 图片列表正常")

    # 测试转发动态
    print("\n2. 测试转发动态 (test2) - 逐步转换")
    print("-"*70)

    with open(input_dir / "test2_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    # 检查 orig
    print(f"   检查 orig 数据")
    orig_raw = raw_data['item']['orig']
    print(f"     orig.modules.module_dynamic.major 存在: {orig_raw['modules']['module_dynamic'].get('major') is not None}")

    if orig_raw['modules']['module_dynamic'].get('major'):
        print(f"     orig major.opus.pics 数量: {len(orig_raw['modules']['module_dynamic']['major']['opus']['pics'])}")

    # 转换 orig
    orig_info = convert(orig_raw, DynamicInfo)
    print(f"\n   转换后 orig.image_urls: {len(orig_info.image_urls)}")
    print(f"   orig.image_urls: {orig_info.image_urls}")

    if len(orig_info.image_urls) == 0:
        print(f"     ❌ 图片列表为空！")
    else:
        print(f"     ✅ 图片列表正常")

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


if __name__ == "__main__":
    import sys
    test_conversion_flow()
