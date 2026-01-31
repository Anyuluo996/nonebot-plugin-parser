"""
测试 BilibiliParser 解析流程
"""

import asyncio
import json


async def test_dynamic_data_structure():
    """测试 DynamicData 结构解析"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "测试 DynamicData 结构解析" + " "*17 + "║")
    print("╚" + "="*68 + "╝")

    # 加载测试数据
    with open('test_dynamic_1159504791855955984.json', 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    print(f"\n原始数据结构:")
    print(f"  item.type: {raw_data['item']['type']}")
    print(f"  item.modules 键: {list(raw_data['item']['modules'].keys())}")

    # 使用 msgspec 转换
    from msgspec import convert
    from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicData, DynamicInfo

    dynamic_data = convert(raw_data, DynamicData)

    print(f"\n转换后的 DynamicData:")
    print(f"  item: {type(dynamic_data.item)}")
    print(f"  orig: {dynamic_data.orig}")

    # 获取 item 信息
    item_info = dynamic_data.item
    print(f"\nDynamicInfo 属性:")
    print(f"  name: {item_info.name}")
    print(f"  avatar: {item_info.avatar}")
    print(f"  title: {item_info.title}")
    print(f"  text: {item_info.text}")
    print(f"  image_urls: {item_info.image_urls}")
    print(f"  timestamp: {item_info.timestamp}")

    # 检查 modules
    print(f"\nDynamicModule:")
    print(f"  module_dynamic: {type(item_info.modules.module_dynamic)}")
    if item_info.modules.module_dynamic:
        print(f"  module_dynamic keys: {list(item_info.modules.module_dynamic.keys())}")
        print(f"  major: {item_info.modules.module_dynamic.get('major')}")

    # 测试图片 URL
    if item_info.image_urls:
        print(f"\n✅ 有 {len(item_info.image_urls)} 张图片:")
        for i, url in enumerate(item_info.image_urls):
            print(f"  {i+1}. {url}")
    else:
        print(f"\n❌ 没有图片")


async def test_parse_opus_flow():
    """测试 parse_opus 完整流程"""

    print(f"\n{'='*70}")
    print("测试 parse_opus 流程")
    print(f"{'='*70}\n")

    # 由于需要初始化 NoneBot，直接模拟流程
    from bilibili_api.opus import Opus

    opus_id = 1159504791855955984

    try:
        opus = Opus(opus_id)
        info = await opus.get_info()

        print(f"Opus.get_info() 返回类型: {type(info)}")
        print(f"顶层键: {list(info.keys()) if isinstance(info, dict) else 'N/A'}")

        if isinstance(info, dict) and 'item' in info:
            item = info['item']
            print(f"\nitem 键: {list(item.keys())}")
            print(f"item.type: {item.get('type')}")

            if 'modules' in item:
                modules = item['modules']
                print(f"modules 键: {list(modules.keys())}")

                if 'module_dynamic' in modules:
                    dynamic = modules['module_dynamic']
                    print(f"\nmodule_dynamic 键: {list(dynamic.keys())}")

                    if 'major' in dynamic:
                        major = dynamic['major']
                        print(f"major 键: {list(major.keys())}")

                        if 'opus' in major:
                            opus_data = major['opus']
                            print(f"\nmajor.opus 键: {list(opus_data.keys())}")

                            if 'pics' in opus_data:
                                pics = opus_data['pics']
                                print(f"\n✅ 找到 {len(pics)} 张图片:")
                                for i, pic in enumerate(pics):
                                    print(f"  {i+1}. {pic.get('url')}")

                            if 'summary' in opus_data:
                                summary = opus_data['summary']
                                print(f"\n文本: {summary.get('text')}")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


async def main():
    await test_dynamic_data_structure()
    await test_parse_opus_flow()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
