"""
渲染 test2_manual_parse.json 数据为图片（简化版）
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def render_test2_simple():
    """简化版渲染 - 直接使用 PIL 绘制"""

    print("="*70)
    print("渲染转发动态 (test2_manual_parse.json) - 简化版")
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

                    downloaded_images.append(image_path)
                    print(f"    ✅ {image_filename} ({len(response.content)} bytes)")
        except Exception as e:
            print(f"    ❌ 下载失败: {e}")

    # 使用 PIL 简单绘制
    print(f"\n开始绘制...")
    try:
        from PIL import Image, ImageDraw, ImageFont

        # 参数配置
        WIDTH = 800
        PADDING = 40
        HEADER_HEIGHT = 100
        FONT_SIZE = 24
        LINE_HEIGHT = 36

        # 计算文本高度
        try:
            font = ImageFont.truetype("msyh.ttc", FONT_SIZE)
            font_bold = ImageFont.truetype("msyhbd.ttc", FONT_SIZE + 4)
        except:
            font = ImageFont.load_default()
            font_bold = font

        # 简单估算文本行数（中文字符按2个宽度计算）
        def get_text_lines(t, width):
            lines = []
            current_line = ""
            for char in t:
                # 中文/全角字符算2个宽度，英文/数字算1个
                char_width = 2 if ord(char) > 127 else 1
                if len(current_line) + char_width > width // 12:
                    lines.append(current_line)
                    current_line = char
                else:
                    current_line += char
            if current_line:
                lines.append(current_line)
            return lines

        text_lines = get_text_lines(text, WIDTH - 2 * PADDING)
        text_height = len(text_lines) * LINE_HEIGHT

        # 计算图片总高度
        images_total_height = 0
        image_previews = []
        for img_path in downloaded_images:
            img = Image.open(img_path)
            # 计算缩放后的高度
            scale = (WIDTH - 2 * PADDING) / img.width
            new_height = int(img.height * scale)
            images_total_height += new_height + 20
            image_previews.append((img, new_height))

        total_height = HEADER_HEIGHT + text_height + images_total_height + PADDING * 2

        print(f"  画布尺寸: {WIDTH} x {total_height}")
        print(f"  文本行数: {len(text_lines)}")
        print(f"  图片数量: {len(image_previews)}")

        # 创建画布
        img = Image.new('RGB', (WIDTH, total_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        y_offset = PADDING

        # 绘制标题（作者名）
        draw.text((PADDING, y_offset), f"@{data['name']}", fill=(0, 0, 0), font=font_bold)
        y_offset += 40

        # 绘制转发信息
        if data.get('forwarder'):
            draw.text((PADDING, y_offset), f"✦ {data['forwarder']} 转发", fill=(128, 128, 128), font=font)
            y_offset += 30

        # 绘制文本
        for line in text_lines:
            draw.text((PADDING, y_offset), line, fill=(0, 0, 0), font=font)
            y_offset += LINE_HEIGHT

        y_offset += 20

        # 绘制图片
        for img_obj, height in image_previews:
            # 缩放图片
            scaled = img_obj.resize((WIDTH - 2 * PADDING, height), Image.Resampling.LANCZOS)
            # 粘贴图片
            img.paste(scaled, (PADDING, y_offset))
            y_offset += height + 20

        # 保存结果
        output_file = Path("tests/pipeline_output/_render_test2_simple.png")
        img.save(output_file, format="PNG")

        print(f"  ✅ 绘制成功")
        print(f"  💾 保存到: {output_file.name}")

        # 检查文件大小
        if output_file.exists():
            size = output_file.stat().st_size
            print(f"  📊 文件大小: {size} bytes ({size/1024:.1f} KB)")
            print(f"  📐 图片尺寸: {img.size[0]} x {img.size[1]}")

    except Exception as e:
        print(f"  ❌ 绘制失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*70}")
    print("渲染完成")
    print(f"{'='*70}")


async def main():
    await render_test2_simple()


if __name__ == "__main__":
    asyncio.run(main())
