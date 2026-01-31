"""
测试 msgspec 转换和渲染流程（使用保存的 JSON 数据）
"""

import asyncio
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_msgspec_conversion():
    """测试 msgspec 转换"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*10 + "Msgspec 转换测试" + " "*34 + "║")
    print("╚" + "="*68 + "╝")
    print(f"\n开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 读取保存的 JSON 数据
    input_dir = Path("tests/pipeline_output")

    test_files = [
        ("test1_raw_api.json", "普通图文动态"),
        ("test2_raw_api.json", "转发动态"),
    ]

    for filename, name in test_files:
        print(f"\n{'='*70}")
        print(f"测试: {name}")
        print(f"文件: {filename}")
        print(f"{'='*70}")

        raw_file = input_dir / filename
        if not raw_file.exists():
            print(f"   ⚠️  文件不存在，跳过")
            continue

        with open(raw_file, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)

        print(f"\n步骤1: 检查原始数据结构")

        item = raw_data.get('item', {})
        item_type = item.get('type', 'N/A')
        print(f"   item.type: {item_type}")

        # 检查 item 的 major
        item_modules = item.get('modules', {})
        item_module_dynamic = item_modules.get('module_dynamic', {})
        item_major = item_module_dynamic.get('major')
        item_desc = item_module_dynamic.get('desc')

        print(f"   item.major: {item_major}")
        if item_desc:
            desc_text = item_desc.get('text', '')
            print(f"   item.desc.text: {desc_text}")

        # 检查 orig
        orig = item.get('orig')
        if orig:
            print(f"\n   ✅ 检测到 orig 字段")
            orig_type = orig.get('type', 'N/A')
            print(f"   orig.type: {orig_type}")

            orig_modules = orig.get('modules', {})
            orig_module_dynamic = orig_modules.get('module_dynamic', {})
            orig_major = orig_module_dynamic.get('major')
            orig_desc = orig_module_dynamic.get('desc')

            print(f"   orig.major: {type(orig_major).__name__ if orig_major else 'None'}")

            if orig_major:
                if 'opus' in orig_major:
                    opus = orig_major['opus']
                    summary = opus.get('summary', {})
                    text = summary.get('text', '')
                    pics = opus.get('pics', [])
                    print(f"   orig.major.opus.summary.text: {text[:50]}...")
                    print(f"   orig.major.opus.pics: {len(pics)} 张图片")
                    for i, pic in enumerate(pics):
                        print(f"     [{i+1}] {pic['url']}")
                elif 'archive' in orig_major:
                    archive = orig_major['archive']
                    print(f"   orig.major.archive: {archive.get('title', 'N/A')}")
            else:
                print(f"   ⚠️  orig.major 为空")

        print(f"\n步骤2: 尝试 msgspec 转换")

        try:
            from msgspec import convert
            from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicData

            dynamic_data = convert(raw_data, DynamicData)
            print(f"   ✅ Msgspec 转换成功")

            print(f"\n步骤3: 检查转换后的数据")

            print(f"   dynamic_data.item.type: {dynamic_data.item.type}")
            print(f"   dynamic_data.orig: {dynamic_data.orig is not None}")

            if dynamic_data.orig:
                print(f"   dynamic_data.orig.type: {dynamic_data.orig.type}")
                print(f"   dynamic_data.orig.text: {dynamic_data.orig.text}")
                print(f"   dynamic_data.orig.image_urls: {len(dynamic_data.orig.image_urls)} 张")

                # 保存转换后的数据
                output_file = input_dir / f"{filename.replace('_raw_api.json', '_msgspec.json')}"
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        "item": {
                            "type": dynamic_data.item.type,
                            "text": dynamic_data.item.text,
                            "image_urls": dynamic_data.item.image_urls,
                        },
                        "orig": {
                            "type": dynamic_data.orig.type,
                            "text": dynamic_data.orig.text,
                            "image_urls": dynamic_data.orig.image_urls,
                        } if dynamic_data.orig else None
                    }, f, ensure_ascii=False, indent=2)

                print(f"   💾 保存到: {output_file.name}")

        except Exception as e:
            print(f"   ❌ Msgspec 转换失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*70}")
    print("测试完成")
    print(f"{'='*70}\n")


async def test_render():
    """测试渲染流程"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*10 + "渲染测试" + " "*38 + "║")
    print("╚" + "="*68 + "╝")
    print(f"\n开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    output_dir = Path("tests/pipeline_output")

    # 测试用例 - 使用手动提取的数据
    test_cases = [
        {
            "name": "转发动态（手动提取）",
            "data": {
                "name": "糊涂的小炎陵",
                "text": "春随淑气融残雪，福伴清音入晓云。\n更待龙腾新岁至，九州同梦物华臻。\n\n除夕夜，乐团圆，这里欢歌载舞，这里有欢声笑语，这里有动听的原创曲，有帅气的MMD舞蹈，有幽默的动画短剧，有美丽的手书。更多的节目情报即将揭秘，敬请期待~\n\n2月16日除夕夜，相约@糊涂的小炎陵 直播间，我们在MeUmy大草原一起纳福迎新，共庆华年~ \n图片为某单品封面",
                "images": [
                    "http://i0.hdslb.com/bfs/new_dyn/136c06e238eb3157e374ae559b0819f83493275791001830.jpg",
                    "http://i0.hdslb.com/bfs/new_dyn/f8bde5b6569ed4f8b99d46711008ebeb3493275791001830.png"
                ],
                "avatar": "https://i2.hdslb.com/bfs/face/69d429be34eb309a6dc0ba30a5525c7265e871ac.jpg",
                "forward_comment": "好耶[咩栗呜米收藏集_大头][咩栗呜米收藏集_大头][咩栗呜米收藏集_大头]",
                "forwarder": "星空future"
            }
        }
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*70}")
        print(f"测试 {i}/{len(test_cases)}: {test_case['name']}")
        print(f"{'='*70}")

        data = test_case['data']

        print(f"\n数据:")
        print(f"  作者: {data['name']}")
        print(f"  文本: {data['text'][:50]}...")
        print(f"  图片: {len(data['images'])} 张")
        print(f"  转发评论: {data.get('forward_comment', 'N/A')}")
        print(f"  转发者: {data.get('forwarder', 'N/A')}")

        # 下载图片
        print(f"\n下载图片...")
        import httpx

        downloaded_images = []
        for j, image_url in enumerate(data['images']):
            print(f"  下载图片{j+1}...")

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Referer": "https://www.bilibili.com",
                    }

                    response = await client.get(image_url, headers=headers)
                    if response.status_code == 200:
                        image_ext = image_url.split('.')[-1] if '.' in image_url.split('?')[0] else 'jpg'
                        image_filename = f"render_test_image{j+1}.{image_ext}"
                        image_path = output_dir / image_filename

                        with open(image_path, 'wb') as f:
                            f.write(response.content)

                        downloaded_images.append(str(image_path))
                        print(f"    ✅ {image_filename} ({len(response.content)} bytes)")
            except Exception as e:
                print(f"    ❌ 下载失败: {e}")

        # 渲染图片
        print(f"\n渲染图片...")
        try:
            from nonebot_plugin_parser.render import Render, RenderParams
            from nonebot_plugin_parser.utils import ReadableImage

            params = RenderParams(
                title=data['name'],
                content=data['text'],
                images=[ReadableImage(p) for p in downloaded_images],
                rounding=True,
                avatar=ReadableImage(data['avatar']) if data.get('avatar') else None
            )

            render = Render()
            image = await render.render(params)

            # 保存渲染结果
            output_file = output_dir / f"_render_test{i}.png"
            image.save(output_file, format="PNG")

            print(f"  ✅ 渲染成功")
            print(f"  💾 保存到: {output_file.name}")

            # 检查图片大小
            if output_file.exists():
                size = output_file.stat().st_size
                print(f"  📊 文件大小: {size} bytes")

        except Exception as e:
            print(f"  ❌ 渲染失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*70}")
    print("测试完成")
    print(f"{'='*70}\n")


async def main():
    await test_msgspec_conversion()
    await test_render()

    print(f"\n" + "="*70)
    print("所有测试完成！")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
