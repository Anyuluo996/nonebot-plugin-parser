"""
渲染 test2_manual_parse.json 数据为图片
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def render_test2():
    """渲染 test2_manual_parse.json"""

    print("="*70)
    print("渲染转发动态 (test2_manual_parse.json)")
    print("="*70)

    # 读取数据
    input_file = Path("tests/pipeline_output/test2_manual_parse.json")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"\n数据:")
    print(f"  作者: {data['name']}")
    print(f"  文本: {data['text'][:80]}...")
    print(f"  图片: {len(data['image_urls'])} 张")
    if data.get('forward_comment'):
        print(f"  转发评论: {data['forward_comment'][:40]}...")
        print(f"  转发者: {data['forwarder']}")

    # 构建最终文本（包含转发评论）
    text = data['text']
    if data.get('forward_comment'):
        if text:
            text = f"{data['forward_comment']}\n\n---\n\n{text}"
        else:
            text = data['forward_comment']

    print(f"\n渲染文本预览:")
    print(f"  {text[:100]}...")

    # 下载图片
    print(f"\n开始下载图片...")
    import httpx

    downloaded_images = []
    for i, image_url in enumerate(data['image_urls']):
        print(f"  下载图片{i+1}...")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                }

                response = await client.get(image_url, headers=headers)
                if response.status_code == 200:
                    image_ext = image_url.split('.')[-1] if '.' in image_url.split('?')[0] else 'jpg'
                    image_filename = f"render_test2_image{i+1}.{image_ext}"
                    image_path = Path("tests/pipeline_output") / image_filename

                    with open(image_path, 'wb') as f:
                        f.write(response.content)

                    downloaded_images.append(str(image_path))
                    print(f"    ✅ {image_filename} ({len(response.content)} bytes)")
        except Exception as e:
            print(f"    ❌ 下载失败: {e}")

    # 渲染图片
    print(f"\n开始渲染...")
    try:
        from nonebot_plugin_parser.render import Render, RenderParams
        from nonebot_plugin_parser.utils import ReadableImage

        params = RenderParams(
            title=data['name'],
            content=text,
            images=[ReadableImage(p) for p in downloaded_images],
            rounding=True,
            avatar=ReadableImage(data['avatar']) if data.get('avatar') else None
        )

        render = Render()
        image = await render.render(params)

        # 保存渲染结果
        output_file = Path("tests/pipeline_output/_render_test2_result.png")
        image.save(output_file, format="PNG")

        print(f"  ✅ 渲染成功")
        print(f"  💾 保存到: {output_file.name}")

        # 检查图片大小
        if output_file.exists():
            size = output_file.stat().st_size
            print(f"  📊 文件大小: {size} bytes ({size/1024:.1f} KB)")

            # 检查图片尺寸
            from PIL import Image
            img = Image.open(output_file)
            print(f"  📐 图片尺寸: {img.size[0]} x {img.size[1]}")

    except Exception as e:
        print(f"  ❌ 渲染失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*70}")
    print("渲染完成")
    print(f"{'='*70}")


async def main():
    await render_test2()


if __name__ == "__main__":
    asyncio.run(main())
