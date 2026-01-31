"""
模拟完整的解析和渲染流程
"""

import asyncio
import json


async def test_full_parse_flow():
    """测试完整的解析流程"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "测试完整解析流程" + " "*19 + "║")
    print("╚" + "="*68 + "╝")

    from bilibili_api.dynamic import Dynamic
    from msgspec import convert

    # 直接导入 dynamic 模块
    import importlib.util
    spec = importlib.util.spec_from_file_location("dynamic", "src/nonebot_plugin_parser/parsers/bilibili/dynamic.py")
    dynamic_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dynamic_module)

    DynamicData = dynamic_module.DynamicData

    opus_id = 1159504791855955984

    print(f"\n1. 获取数据:")
    print(f"   ID: {opus_id}")

    dynamic = Dynamic(opus_id)
    raw_data = await dynamic.get_info()

    print(f"   ✅ 获取成功")

    print(f"\n2. 转换数据:")

    dynamic_data = convert(raw_data, DynamicData)
    dynamic_info = dynamic_data.item

    print(f"   ✅ 转换成功")
    print(f"   name: {dynamic_info.name}")
    print(f"   text: {dynamic_info.text}")
    print(f"   image_urls: {dynamic_info.image_urls}")

    print(f"\n3. 模拟 parse_dynamic 方法:")

    # 模拟 parse_dynamic 的逻辑
    author_name = dynamic_info.name
    author_avatar = dynamic_info.avatar
    text = dynamic_info.text
    image_urls = dynamic_info.image_urls

    print(f"   author: {author_name}")
    print(f"   text: {text}")
    print(f"   图片数量: {len(image_urls)}")

    if image_urls:
        print(f"   图片列表:")
        for i, url in enumerate(image_urls):
            print(f"     {i+1}. {url}")

    print(f"\n4. 模拟 ParseResult:")

    # 检查 contents 是否为空
    if image_urls:
        print(f"   ✅ contents 不为空，有 {len(image_urls)} 个 ImageContent")
    else:
        print(f"   ❌ contents 为空")

    if text:
        print(f"   ✅ text 不为空: {text}")
    else:
        print(f"   ❌ text 为空")

    print(f"\n5. 检查数据类型:")
    print(f"   item.type: {dynamic_info.type}")

    # 检查是否是转发类型
    if dynamic_data.orig:
        print(f"   有 orig (转发): ✅")
        print(f"   orig.type: {dynamic_data.orig.type}")
    else:
        print(f"   有 orig (转发): ❌")

    print(f"\n总结:")
    if text and image_urls:
        print(f"   ✅ 既有文本又有图片，应该正常发送")
    elif text:
        print(f"   ⚠️  只有文本，没有图片")
    elif image_urls:
        print(f"   ⚠️  只有图片，没有文本")
    else:
        print(f"   ❌ 既没有文本也没有图片")


async def main():
    await test_full_parse_flow()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
