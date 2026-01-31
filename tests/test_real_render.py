"""
使用实际解析器测试 B站动态渲染
测试链接: https://m.bilibili.com/opus/1159504791855955984
"""

import asyncio
import sys
import os
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def test_bilibili_render():
    """测试 B站动态的完整解析和渲染流程"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "B站动态渲染测试" + " "*31 + "║")
    print("╚" + "="*68 + "╝")

    test_url = "https://m.bilibili.com/opus/1159504791855955984"
    dynamic_id = 1159504791855955984

    print(f"\n测试链接: {test_url}")
    print(f"动态 ID: {dynamic_id}")

    try:
        from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

        # 创建解析器实例
        parser = BilibiliParser()

        print(f"\n步骤1: 初始化凭证...")
        credential = await parser.credential
        print(f"   凭证状态: {'✅ 已配置' if credential else '⚠️  未配置'}")

        print(f"\n步骤2: 解析动态...")
        result = await parser.parse_dynamic(dynamic_id)

        print(f"   ✅ 解析成功")
        print(f"\n解析结果:")
        print(f"  平台: {result.platform.display_name}")
        print(f"  作者: {result.author.name if result.author else 'N/A'}")
        print(f"  头像: {result.author.avatar if result.author else 'N/A'}")
        print(f"  标题: {result.title}")
        print(f"  文本: {result.text}")
        print(f"  时间戳: {result.timestamp}")
        print(f"  内容数量: {len(result.contents)}")
        print(f"  图片数量: {len(result.img_contents)}")

        if result.img_contents:
            print(f"\n  图片列表:")
            for i, img_content in enumerate(result.img_contents, 1):
                print(f"    [{i}] ImageContent")

        print(f"\n步骤3: 下载图片...")

        # 下载图片
        from nonebot_plugin_parser.parsers import ImageContent
        downloaded_images = []

        for i, content in enumerate(result.contents, 1):
            if isinstance(content, ImageContent):
                print(f"  下载图片 {i}/{len(result.contents)}...")
                try:
                    img_path = await content.get_path()
                    if img_path:
                        downloaded_images.append(img_path)
                        print(f"    ✅ {img_path.name} ({img_path.stat().st_size / 1024:.1f} KB)")
                except Exception as e:
                    print(f"    ❌ 下载失败: {e}")

        print(f"\n  成功下载: {len(downloaded_images)}/{len(result.img_contents)} 张图片")

        if len(downloaded_images) == 0:
            print(f"\n  ⚠️  没有成功下载任何图片，无法进行渲染测试")
            return

        print(f"\n步骤4: 渲染卡片...")

        # 尝试使用 CommonRenderer 渲染
        try:
            from nonebot_plugin_parser.renders.common import CommonRenderer
            from nonebot_plugin_parser.utils import ReadableImage

            renderer = CommonRenderer()

            # 创建渲染参数
            from nonebot_plugin_parser.renders.common import RenderParams

            params = RenderParams(
                title=result.author.name if result.author else "B站动态",
                content=result.text or "",
                images=[ReadableImage(str(p)) for p in downloaded_images],
                rounding=True,
                avatar=ReadableImage(result.author.avatar) if result.author and result.author.avatar else None
            )

            print(f"  使用 CommonRenderer 渲染...")
            image = await renderer.render(params)

            # 保存渲染结果
            output_dir = Path("tests/pipeline_output")
            output_dir.mkdir(exist_ok=True)
            output_file = output_dir / "_real_render_test.png"

            from io import BytesIO
            img_bytes = BytesIO()
            image.save(img_bytes, format='PNG')
            img_bytes.seek(0)

            # 保存到文件
            with open(output_file, 'wb') as f:
                f.write(img_bytes.read())

            print(f"  ✅ 渲染成功")
            print(f"  💾 保存到: {output_file.name}")

            # 检查文件大小
            if output_file.exists():
                size = output_file.stat().st_size
                print(f"  📊 文件大小: {size} bytes ({size/1024:.1f} KB)")

                # 检查图片尺寸
                from PIL import Image
                img = Image.open(output_file)
                print(f"  📐 图片尺寸: {img.size[0]} x {img.size[1]}")

        except ImportError as e:
            print(f"  ⚠️  CommonRenderer 不可用: {e}")
            print(f"  跳过渲染测试")
        except Exception as e:
            print(f"  ❌ 渲染失败: {e}")
            import traceback
            traceback.print_exc()

        print(f"\n步骤5: 诊断信息")

        issues = []

        if not result.text:
            issues.append("⚠️  文本为空")
        else:
            print(f"  ✅ 文本: {result.text[:50]}...")

        if len(result.img_contents) == 0:
            issues.append("⚠️  图片列表为空")
        else:
            print(f"  ✅ 图片数量: {len(result.img_contents)}")

        if len(downloaded_images) == 0:
            issues.append("⚠️  没有成功下载任何图片")
        else:
            print(f"  ✅ 下载成功: {len(downloaded_images)} 张")

        if not issues:
            print(f"\n  ✅ 所有检查项都通过！")
        else:
            print(f"\n  ⚠️  发现 {len(issues)} 个问题:")
            for issue in issues:
                print(f"     {issue}")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*70}")
    print("测试完成")
    print(f"{'='*70}\n")


async def main():
    await test_bilibili_render()


if __name__ == "__main__":
    asyncio.run(main())
