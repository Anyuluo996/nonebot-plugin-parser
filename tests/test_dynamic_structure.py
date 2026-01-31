"""
测试 bilibili 解析逻辑（不依赖 NoneBot）
"""

import asyncio
import json
import sys
import os

# 直接导入，不通过 __init__
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_dynamic_structure():
    """测试 DynamicData 结构"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "测试 DynamicData 解析逻辑" + " "*17 + "║")
    print("╚" + "="*68 + "╝")

    # 加载测试数据
    with open('test_dynamic_1159504791855955984.json', 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    print(f"\n1. 原始数据:")
    print(f"   item.type: {raw_data['item']['type']}")
    print(f"   item.modules 键: {list(raw_data['item']['modules'].keys())}")

    # 导入 msgspec 和结构体
    from msgspec import convert

    # 直接导入模块
    import importlib.util
    spec = importlib.util.spec_from_file_location("dynamic", "src/nonebot_plugin_parser/parsers/bilibili/dynamic.py")
    dynamic_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dynamic_module)

    DynamicData = dynamic_module.DynamicData

    # 转换数据
    dynamic_data = convert(raw_data, DynamicData)

    print(f"\n2. DynamicData 转换:")
    print(f"   item: {type(dynamic_data.item)}")

    # 获取 item 信息
    item_info = dynamic_data.item
    print(f"\n3. DynamicInfo 属性:")

    # 测试各个属性
    try:
        print(f"   name: {item_info.name}")
    except Exception as e:
        print(f"   name: ❌ {e}")

    try:
        print(f"   avatar: {item_info.avatar[:50]}...")
    except Exception as e:
        print(f"   avatar: ❌ {e}")

    try:
        print(f"   title: {item_info.title}")
    except Exception as e:
        print(f"   title: ❌ {e}")

    try:
        print(f"   text: {item_info.text}")
    except Exception as e:
        print(f"   text: ❌ {e}")

    try:
        print(f"   image_urls: {item_info.image_urls}")
    except Exception as e:
        print(f"   image_urls: ❌ {e}")

    try:
        print(f"   timestamp: {item_info.timestamp}")
    except Exception as e:
        print(f"   timestamp: ❌ {e}")

    # 检查 modules
    print(f"\n4. DynamicModule 检查:")
    print(f"   module_dynamic 类型: {type(item_info.modules.module_dynamic)}")

    if item_info.modules.module_dynamic:
        print(f"   module_dynamic 键: {list(item_info.modules.module_dynamic.keys())}")
        major = item_info.modules.module_dynamic.get('major')
        print(f"   major: {type(major)}")

        if major:
            print(f"   major 键: {list(major.keys())}")
            if 'opus' in major:
                opus = major['opus']
                print(f"   major.opus 键: {list(opus.keys())}")
                print(f"   major.opus.pics: {len(opus.get('pics', []))} 张")


async def main():
    await test_dynamic_structure()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
