"""
测试指定链接的解析
"""

import asyncio
import sys
import os

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_parse_url():
    """测试解析 URL"""

    url = "https://m.bilibili.com/opus/1159504791855955984"

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + f"测试解析: {url}" + " "*15 + "║")
    print("╚" + "="*68 + "╝")

    # 1. 测试 bilibili_api 库直接调用
    print(f"\n{'='*70}")
    print("测试1: bilibili_api.opus.Opus")
    print(f"{'='*70}\n")

    opus_id = 1159504791855955984

    try:
        from bilibili_api.opus import Opus

        opus = Opus(opus_id)
        info = await opus.get_info()

        print(f"✅ Opus 接口成功")
        print(f"   类型: {type(info)}")

        if isinstance(info, dict):
            print(f"   键: {list(info.keys())[:10]}")

            # 保存数据
            import json
            with open('test_opus_1159504791855955984.json', 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            print(f"   💾 数据已保存: test_opus_1159504791855955984.json")

    except Exception as e:
        print(f"❌ Opus 接口失败: {type(e).__name__}: {e}")

    # 2. 测试 Dynamic 接口
    print(f"\n{'='*70}")
    print("测试2: bilibili_api.dynamic.Dynamic")
    print(f"{'='*70}\n")

    try:
        from bilibili_api.dynamic import Dynamic

        dynamic = Dynamic(opus_id)
        info = await dynamic.get_info()

        print(f"✅ Dynamic 接口成功")
        print(f"   类型: {type(info)}")

        if isinstance(info, dict):
            item = info.get('item', {})
            print(f"   item.type: {item.get('type')}")
            print(f"   item 键: {list(item.keys())[:10]}")

            # 检查是否有 orig
            if 'orig' in item:
                print(f"   有 orig (转发): ✅")
                orig = item['orig']
                if 'modules' in orig:
                    orig_modules = orig['modules']
                    if 'module_dynamic' in orig_modules:
                        orig_dynamic = orig_modules['module_dynamic']
                        if 'major' in orig_dynamic:
                            major = orig_dynamic['major']
                            if major and 'opus' in major:
                                opus = major['opus']
                                if 'pics' in opus:
                                    pics = opus['pics']
                                    print(f"   原动态图片数: {len(pics)}")
                                    for i, pic in enumerate(pics[:3]):
                                        print(f"     图片{i+1}: {pic.get('url', 'N/A')}")
                            else:
                                print(f"   major: {list(major.keys()) if major else 'None'}")
            else:
                print(f"   有 orig (转发): ❌")
                # 检查是否有图片
                if 'modules' in item:
                    modules = item['modules']
                    if 'module_dynamic' in modules:
                        dynamic_module = modules['module_dynamic']
                        if 'major' in dynamic_module:
                            major = dynamic_module['major']
                            if major and 'opus' in major:
                                opus = major['opus']
                                if 'pics' in opus:
                                    pics = opus['pics']
                                    print(f"   图片数: {len(pics)}")
                                    for i, pic in enumerate(pics[:3]):
                                        print(f"     图片{i+1}: {pic.get('url', 'N/A')}")

            # 保存数据
            import json
            with open('test_dynamic_1159504791855955984.json', 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            print(f"   💾 数据已保存: test_dynamic_1159504791855955984.json")

    except Exception as e:
        print(f"❌ Dynamic 接口失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # 3. 测试 Parser
    print(f"\n{'='*70}")
    print("测试3: BilibiliParser.parse_opus")
    print(f"{'='*70}\n")

    try:
        from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

        parser = BilibiliParser()
        result = await parser.parse_opus(opus_id)

        print(f"✅ Parser 成功")
        print(f"   标题: {result.title}")
        print(f"   作者: {result.author.name if result.author else 'N/A'}")
        print(f"   文本: {result.text[:100] if result.text else 'N/A'}...")
        print(f"   内容数量: {len(result.contents)}")

        for i, content in enumerate(result.contents[:5]):
            print(f"   内容{i+1}: {type(content).__name__}")

    except Exception as e:
        print(f"❌ Parser 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # 4. 测试 URL 匹配
    print(f"\n{'='*70}")
    print("测试4: URL 匹配")
    print(f"{'='*70}\n")

    try:
        from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

        parser = BilibiliParser()
        keyword, searched = parser.search_url(url)

        print(f"✅ 匹配成功")
        print(f"   关键词: {keyword}")
        print(f"   匹配内容: {searched.group(0)}")

    except Exception as e:
        print(f"❌ 匹配失败: {type(e).__name__}: {e}")


async def main():
    await test_parse_url()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
