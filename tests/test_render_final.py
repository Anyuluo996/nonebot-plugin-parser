"""
简化版渲染测试 - 1159504791855955984
不依赖 msgspec，直接使用原始数据
"""

import asyncio
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


async def test_render_simple():
    """简化版渲染测试"""

    print("="*70)
    print("简化版渲染测试: 1159504791855955984")
    print("="*70)

    input_dir = Path("tests/pipeline_output")

    # 读取数据
    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    print("\n步骤1: 提取数据")
    print("-"*70)

    item = raw_data['item']
    modules = item['modules']
    module_author = modules['module_author']
    module_dynamic = modules['module_dynamic']
    major = module_dynamic.get('major', {})

    # 作者信息
    author_name = module_author.get('name', '')
    pub_time = module_author.get('pub_time', '')

    print(f"  作者: {author_name}")
    print(f"  类型: {item.get('type')}")
    print(f"  major.type: {major.get('type')}")

    # 提取内容（模拟 DynamicInfo.text 的逻辑）
    text = None
    if 'opus' in major:
        opus = major['opus']
        summary = opus.get('summary', {})
        text = summary.get('text')
    elif 'draw' in major:
        # DRAW 类型的文本在 desc 中
        desc = module_dynamic.get('desc', {})
        text = desc.get('text')

    print(f"  文本: {text}")

    # 提取图片（模拟 DynamicInfo.image_urls 的逻辑）
    image_urls = []
    if 'opus' in major:
        opus = major['opus']
        pics = opus.get('pics', [])
        image_urls = [pic['url'] for pic in pics]
    elif 'draw' in major:
        draw = major['draw']
        items = draw.get('items', [])
        image_urls = [item['src'] for item in items]

    print(f"  图片数: {len(image_urls)}")

    print("\n步骤2: 下载图片")
    print("-"*70)

    import httpx

    downloaded_images = []
    for i, image_url in enumerate(image_urls):
        print(f"  下载图片{i+1}...")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                }

                response = await client.get(image_url, headers=headers)
                if response.status_code == 200:
                    image_path = input_dir / f"_final_test_{i+1}.jpg"

                    with open(image_path, 'wb') as f:
                        f.write(response.content)

                    downloaded_images.append(image_path)
                    print(f"    ✅ {image_path.name} ({len(response.content)} bytes)")
        except Exception as e:
            print(f"    ❌ 下载失败: {e}")

    print(f"\n  成功: {len(downloaded_images)}/{len(image_urls)} 张")

    if not downloaded_images:
        print("\n❌ 没有图片可以渲染")
        return

    print("\n步骤3: 渲染图片")
    print("-"*70)

    # 配置
    WIDTH = 800
    PADDING = 30
    FONT_SIZE = 24
    LINE_HEIGHT = 36

    # 加载字体
    try:
        font = ImageFont.truetype("msyh.ttc", FONT_SIZE)
        font_bold = ImageFont.truetype("msyhbd.ttc", FONT_SIZE + 6)
        font_small = ImageFont.truetype("msyh.ttc", FONT_SIZE - 4)
    except:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", FONT_SIZE)
            font_bold = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", FONT_SIZE + 6)
            font_small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", FONT_SIZE - 4)
        except:
            font = ImageFont.load_default()
            font_bold = font
            font_small = font

    # 估算文本行数
    def get_text_lines(t, width):
        lines = []
        current_line = ""
        for char in t:
            char_width = 2 if ord(char) > 127 else 1
            if len(current_line) + char_width > width // 13:
                lines.append(current_line)
                current_line = char
            else:
                current_line += char
        if current_line:
            lines.append(current_line)
        return lines

    text_lines = get_text_lines(text or "", WIDTH - 2 * PADDING)
    text_height = len(text_lines) * LINE_HEIGHT if text_lines else 0

    # 计算图片高度
    images_total_height = 0
    image_previews = []
    for img_path in downloaded_images:
        img = Image.open(img_path)
        scale = min(1.0, (WIDTH - 2 * PADDING) / img.width)
        new_width = int(img.width * scale)
        new_height = int(img.height * scale)
        images_total_height += new_height + 15
        image_previews.append((img, new_width, new_height))

    total_height = 120 + text_height + images_total_height + PADDING * 2

    print(f"  画布: {WIDTH} x {total_height}")

    # 创建画布
    img = Image.new('RGB', (WIDTH, total_height), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    y_offset = PADDING

    # 平台标识
    draw.rectangle([PADDING, y_offset, PADDING + 80, y_offset + 30], fill=(251, 114, 153))
    draw.text((PADDING + 10, y_offset + 5), "Bilibili", fill=(255, 255, 255), font=font_small)

    y_offset += 40

    # 作者
    draw.text((PADDING, y_offset), f"@{author_name}", fill=(0, 0, 0), font=font_bold)
    y_offset += 35

    draw.text((PADDING, y_offset), pub_time, fill=(128, 128, 128), font=font_small)
    y_offset += 30

    # 文本
    if text_lines:
        for line in text_lines:
            draw.text((PADDING, y_offset), line, fill=(0, 0, 0), font=font)
            y_offset += LINE_HEIGHT

    y_offset += 10

    # 图片
    for img_obj, width, height in image_previews:
        if img_obj.width > WIDTH - 2 * PADDING:
            scaled = img_obj.resize((WIDTH - 2 * PADDING, height), Image.Resampling.LANCZOS)
        else:
            scaled = img_obj
        x_offset = (WIDTH - scaled.width) // 2
        img.paste(scaled, (x_offset, y_offset))
        y_offset += scaled.height + 15

    # 保存
    output_file = input_dir / "_final_render_1159504791855955984.png"
    img.save(output_file, format="PNG")

    print(f"\n  ✅ 渲染成功")
    print(f"  💾 {output_file.name}")

    if output_file.exists():
        size = output_file.stat().st_size
        print(f"  📊 {size} bytes ({size/1024:.1f} KB)")
        print(f"  📐 {img.size[0]} x {img.size[1]}")

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


async def main():
    await test_render_simple()


if __name__ == "__main__":
    asyncio.run(main())
