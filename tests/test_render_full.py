"""
完整测试B站动态解析和渲染流程
生成渲染结果供查看
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_full_render():
    """测试完整的解析和渲染流程"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*10 + "完整测试 B站动态解析和渲染流程" + " "*11 + "║")
    print("╚" + "="*68 + "╝")

    # 测试 ID
    opus_id = 1159504791855955984

    print(f"\n测试 ID: {opus_id}")

    # 1. 获取 API 数据
    print(f"\n{'='*70}")
    print("步骤1: 获取 API 数据")
    print(f"{'='*70}\n")

    from bilibili_api.dynamic import Dynamic
    from msgspec import convert

    # 直接导入 dynamic 模块
    import importlib.util
    spec = importlib.util.spec_from_file_location("dynamic", "src/nonebot_plugin_parser/parsers/bilibili/dynamic.py")
    dynamic_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dynamic_module)

    DynamicData = dynamic_module.DynamicData

    dynamic = Dynamic(opus_id)
    raw_data = await dynamic.get_info()

    print(f"✅ API 数据获取成功")
    print(f"   item.type: {raw_data['item']['type']}")

    # 保存原始 API 数据
    with open('tests/render_raw_api_data.json', 'w', encoding='utf-8') as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    print(f"   💾 原始数据已保存: tests/render_raw_api_data.json")

    # 2. 转换为结构化数据
    print(f"\n{'='*70}")
    print("步骤2: 转换为结构化数据")
    print(f"{'='*70}\n")

    dynamic_data = convert(raw_data, DynamicData)
    dynamic_info = dynamic_data.item

    print(f"✅ 数据转换成功")
    print(f"   name: {dynamic_info.name}")
    print(f"   text: {dynamic_info.text}")
    print(f"   image_urls 数量: {len(dynamic_info.image_urls)}")
    for i, url in enumerate(dynamic_info.image_urls):
        print(f"     {i+1}. {url}")

    # 3. 模拟 parse_dynamic 创建 ParseResult
    print(f"\n{'='*70}")
    print("步骤3: 创建 ParseResult")
    print(f"{'='*70}\n")

    # 导入必要的模块
    from nonebot_plugin_parser.parsers.data import Author, ParseResult, Platform, ImageContent
    from nonebot_plugin_parser.parsers.base import BaseParser
    from nonebot_plugin_parser.download import DOWNLOADER

    # 创建 parser 实例来使用 helper 方法
    parser = BaseParser()
    parser.__init__()  # 初始化 headers

    # 创建 author
    author = parser.create_author(dynamic_info.name, dynamic_info.avatar)

    # 创建图片内容
    contents = []
    for image_url in dynamic_info.image_urls:
        img_task = DOWNLOADER.download_img(image_url, ext_headers=parser.headers)
        contents.append(ImageContent(img_task))

    print(f"✅ ParseResult 创建成功")
    print(f"   author: {author.name}")
    print(f"   text: {dynamic_info.text}")
    print(f"   contents 数量: {len(contents)}")

    # 创建 platform
    platform = Platform(name="bilibili", display_name="哔哩哔哩")

    # 创建 ParseResult
    result = ParseResult(
        platform=platform,
        author=author,
        title=None,
        text=dynamic_info.text,
        timestamp=dynamic_info.timestamp,
        contents=contents,
    )

    print(f"\n{'='*70}")
    print("步骤4: 下载图片")
    print(f"{'='*70}\n")

    # 等待图片下载
    downloaded_paths = []
    for i, content in enumerate(contents):
        try:
            path = await content.get_path()
            downloaded_paths.append(path)
            print(f"   ✅ 图片{i+1} 下载成功: {path.name}")
        except Exception as e:
            print(f"   ❌ 图片{i+1} 下载失败: {e}")

    print(f"\n   总共下载: {len(downloaded_paths)} 张图片")

    # 5. 模拟渲染
    print(f"\n{'='*70}")
    print("步骤5: 模拟渲染消息")
    print(f"{'='*70}\n")

    # 创建输出目录
    output_dir = Path("tests/render_output")
    output_dir.mkdir(exist_ok=True)

    # 保存渲染信息
    render_info = {
        "parse_result": {
            "platform": result.platform.name,
            "author": {
                "name": result.author.name if result.author else None,
                "avatar": str(result.author.avatar) if result.author and result.author.avatar else None
            } if result.author else None,
            "title": result.title,
            "text": result.text,
            "timestamp": result.timestamp,
            "contents_count": len(result.contents),
        },
        "downloaded_images": [
            {
                "path": str(path),
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0
            }
            for path in downloaded_paths
        ]
    }

    with open(output_dir / 'render_info.json', 'w', encoding='utf-8') as f:
        json.dump(render_info, f, ensure_ascii=False, indent=2)

    print(f"✅ 渲染信息已保存: {output_dir / 'render_info.json'}")

    # 6. 检查问题
    print(f"\n{'='*70}")
    print("步骤6: 问题诊断")
    print(f"{'='*70}\n")

    issues = []

    if not result.text:
        issues.append("❌ text 为空")
    else:
        print(f"   ✅ text 不为空: {result.text}")

    if not result.contents:
        issues.append("❌ contents 为空")
    else:
        print(f"   ✅ contents 不为空: {len(result.contents)} 个内容")

    if not downloaded_paths:
        issues.append("❌ 没有成功下载图片")
    else:
        print(f"   ✅ 成功下载 {len(downloaded_paths)} 张图片")

    # 检查图片文件是否存在
    for i, path in enumerate(downloaded_paths):
        if path.exists():
            size = path.stat().st_size
            if size > 0:
                print(f"   ✅ 图片{i+1} 存在且有效: {size} 字节")
            else:
                issues.append(f"❌ 图片{i+1} 大小为 0")
                print(f"   ❌ 图片{i+1} 大小为 0: {path}")
        else:
            issues.append(f"❌ 图片{i+1} 文件不存在")
            print(f"   ❌ 图片{i+1} 文件不存在: {path}")

    print(f"\n{'='*70}")
    print("总结")
    print(f"{'='*70}\n")

    if issues:
        print(f"发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print(f"✅ 所有检查通过，应该可以正常显示")

    print(f"\n生成的文件:")
    print(f"  1. tests/render_raw_api_data.json - 原始 API 数据")
    print(f"  2. tests/render_output/render_info.json - 渲染信息")
    print(f"  3. tests/render_output/ - 下载的图片文件")


async def test_render_with_different_types():
    """测试不同类型的动态"""

    print(f"\n{'='*70}")
    print("补充测试: 不同类型的动态")
    print(f"{'='*70}\n")

    test_cases = [
        ("普通图文", 1159504791855955984),  # DYNAMIC_TYPE_DRAW
        ("转发动态", 1156587796127809560),   # DYNAMIC_TYPE_FORWARD
    ]

    for name, dynamic_id in test_cases:
        print(f"\n测试: {name} (ID: {dynamic_id})")

        try:
            from bilibili_api.dynamic import Dynamic

            dynamic = Dynamic(dynamic_id)
            raw_data = await dynamic.get_info()

            item_type = raw_data['item']['type']
            has_orig = 'orig' in raw_data['item']

            # 检查图片
            modules = raw_data['item'].get('modules', {})
            module_dynamic = modules.get('module_dynamic', {})
            major = module_dynamic.get('major', {})

            image_count = 0
            if 'opus' in major:
                image_count = len(major['opus'].get('pics', []))
            elif 'archive' in major:
                image_count = 1 if major['archive'].get('cover') else 0

            print(f"   类型: {item_type}")
            print(f"   转发: {'是' if has_orig else '否'}")
            print(f"   图片数: {image_count}")

            if image_count > 0:
                print(f"   ✅ 有图片内容")
            else:
                print(f"   ⚠️  无图片内容")

        except Exception as e:
            print(f"   ❌ 失败: {e}")


async def main():
    await test_full_render()
    await test_render_with_different_types()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
