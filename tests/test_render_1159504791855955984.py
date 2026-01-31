"""
测试 B站动态解析和渲染 - 1159504791855955984
"""

import asyncio
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


async def test_parse_and_render():
    """测试解析和渲染流程"""

    print("="*70)
    print("测试 B站动态: https://www.bilibili.com/opus/1159504791855955984")
    print("="*70)

    # 读取之前保存的数据
    input_dir = Path("tests/pipeline_output")
    with open(input_dir / "test1_raw_api.json", 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    print("\n步骤1: 分析原始数据结构")
    print("-"*70)

    item = raw_data['item']
    modules = item['modules']
    module_dynamic = modules['module_dynamic']
    major = module_dynamic.get('major')

    print(f"  item.type: {item.get('type')}")
    print(f"  major.type: {major.get('type') if major else 'None'}")
    print(f"  major.opus 存在: {'opus' in major if major else False}")
    print(f"  major.draw 存在: {'draw' in major if major else False}")

    if major and 'opus' in major:
        opus = major['opus']
        print(f"  opus.pics 数量: {len(opus.get('pics', []))}")
        print(f"  opus.summary.text: {opus.get('summary', {}).get('text', 'N/A')}")
    elif major and 'draw' in major:
        draw = major['draw']
        print(f"  draw.items 数量: {len(draw.get('items', []))}")

    print("\n步骤2: 模拟 parse_dynamic 逻辑（新代码）")
    print("-"*70)

    # 模拟新代码的解析逻辑
    from msgspec import convert
    from nonebot_plugin_parser.parsers.bilibili.dynamic import DynamicData, DynamicInfo

    # 转换数据
    dynamic_data = convert(raw_data, DynamicData)
    current_info = dynamic_data.item
    content_source = current_info  # 非转发类型，内容来源就是当前动态

    print(f"  current_info.name: {current_info.name}")
    print(f"  current_info.text (优先 major.text): {current_info.text}")
    print(f"  current_info.desc_text: {current_info.desc_text}")
    print(f"  current_info.image_urls 数量: {len(current_info.image_urls)}")

    # 获取标题和文本
    title = content_source.title
    text = current_info.text

    if not title:
        preview_text = text.replace("\n", " ") if text else ""
        title = preview_text[:30] + "..." if len(preview_text) > 30 else preview_text

    print(f"\n  最终标题: {title}")
    print(f"  最终文本: {text}")
    print(f"  图片数量: {len(content_source.image_urls)}")

    print("\n步骤3: 下载图片")
    print("-"*70)

    import httpx

    downloaded_images = []
    for i, image_url in enumerate(content_source.image_urls):
        print(f"  下载图片{i+1}...")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                }

                response = await client.get(image_url, headers=headers)
                if response.status_code == 200:
                    image_path = input_dir / f"_test_1159504791855955984_{i+1}.jpg"

                    with open(image_path, 'wb') as f:
                        f.write(response.content)

                    downloaded_images.append(image_path)
                    print(f"    ✅ {image_path.name} ({len(response.content)} bytes)")
        except Exception as e:
            print(f"    ❌ 下载失败: {e}")

    print(f"\n  成功下载: {len(downloaded_images)} 张图片")

    print("\n步骤4: 渲染卡片")
    print("-"*70)

    # 参数配置
    WIDTH = 800
    PADDING = 30
    HEADER_HEIGHT = 120
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

    # 计算图片总高度
    images_total_height = 0
    image_previews = []
    for img_path in downloaded_images:
        img = Image.open(img_path)
        scale = min(1.0, (WIDTH - 2 * PADDING) / img.width)
        new_width = int(img.width * scale)
        new_height = int(img.height * scale)
        images_total_height += new_height + 15
        image_previews.append((img, new_width, new_height))

    total_height = HEADER_HEIGHT + text_height + images_total_height + PADDING * 2

    print(f"  画布尺寸: {WIDTH} x {total_height}")

    # 创建画布
    img = Image.new('RGB', (WIDTH, total_height), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    y_offset = PADDING

    # 绘制平台标识
    draw.rectangle([PADDING, y_offset, PADDING + 80, y_offset + 30], fill=(251, 114, 153))
    draw.text((PADDING + 10, y_offset + 5), "Bilibili", fill=(255, 255, 255), font=font_small)

    y_offset += 40

    # 绘制作者信息
    draw.text((PADDING, y_offset), f"@{current_info.name}", fill=(0, 0, 0), font=font_bold)
    y_offset += 35

    pub_time = modules['module_author'].get('pub_time', '')
    draw.text((PADDING, y_offset), pub_time, fill=(128, 128, 128), font=font_small)
    y_offset += 30

    # 绘制标题
    if title:
        draw.text((PADDING, y_offset), f"【{title}】", fill=(50, 50, 50), font=font)
        y_offset += 35

    # 绘制文本
    if text_lines:
        for line in text_lines:
            draw.text((PADDING, y_offset), line, fill=(0, 0, 0), font=font)
            y_offset += LINE_HEIGHT

    y_offset += 10

    # 绘制图片
    for img_obj, width, height in image_previews:
        if img_obj.width > WIDTH - 2 * PADDING:
            scaled = img_obj.resize((WIDTH - 2 * PADDING, height), Image.Resampling.LANCZOS)
        else:
            scaled = img_obj
        x_offset = (WIDTH - scaled.width) // 2
        img.paste(scaled, (x_offset, y_offset))
        y_offset += scaled.height + 15

    # 保存结果
    output_file = input_dir / "_test_1159504791855955984_render.png"
    img.save(output_file, format="PNG")

    print(f"\n  ✅ 渲染成功")
    print(f"  💾 保存到: {output_file.name}")

    # 检查文件
    if output_file.exists():
        size = output_file.stat().st_size
        print(f"  📊 文件大小: {size} bytes ({size/1024:.1f} KB)")
        print(f"  📐 图片尺寸: {img.size[0]} x {img.size[1]}")

    print("\n步骤5: 诊断")
    print("-"*70)

    issues = []

    if not text:
        issues.append("⚠️  文本为空")
    else:
        print(f"  ✅ 文本: {text}")

    if len(downloaded_images) == 0:
        issues.append("❌ 图片列表为空")
    else:
        print(f"  ✅ 图片: {len(downloaded_images)} 张")

    if not issues:
        print(f"\n  ✅ 所有检查项通过！")
    else:
        print(f"\n  ⚠️  发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"     {issue}")

    print("\n" + "="*70)
    print("测试完成")
    print("="*70)


async def main():
    await test_parse_and_render()


if __name__ == "__main__":
    asyncio.run(main())
