"""
测试推特 GIF 转换功能

测试链接：
- GIF: https://x.com/i/status/2017344867878248543
- 视频: https://x.com/i/status/2017206656862658581

注意：这是一个演示脚本，实际使用需要在 NoneBot2 机器人中使用。
"""

import asyncio
import tempfile
from pathlib import Path


async def demo_ffprobe():
    """演示 ffprobe 检测音频流功能"""
    print("=" * 60)
    print("演示 1: ffprobe 检测音频流")
    print("=" * 60)

    # 从 utils 模块导入函数
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    # 模拟检测结果
    test_cases = [
        ("video_with_audio.mp4", True, "有音频流（普通视频）"),
        ("video_no_audio.mp4", False, "无音频流（GIF 视频）"),
        ("gif_video.mp4", False, "无音频流（GIF 视频）"),
    ]

    for filename, has_audio, description in test_cases:
        status = "🔊" if has_audio else "🔇"
        print(f"{status} {filename:25s} - {description}")

    print("\n说明:")
    print("  - 有音频流：这是普通视频，不转换为 GIF")
    print("  - 无音频流：这是 GIF 视频，将转换为 GIF 格式")


async def demo_gif_conversion():
    """演示 GIF 转换流程"""
    print("\n" + "=" * 60)
    print("演示 2: GIF 转换流程（使用 palettegen 滤镜）")
    print("=" * 60)

    print("\n第 1 步：生成调色板 (palettegen)")
    print("┌─────────────────────────────────────────────┐")
    print("│ ffmpeg -i input.mp4                        │")
    print("│   -vf fps=15,scale=480:-1:flags=lanczos,   │")
    print("│        palettegen                           │")
    print("│   palette.png                               │")
    print("└─────────────────────────────────────────────┘")

    print("\n第 2 步：使用调色板生成 GIF (paletteuse)")
    print("┌─────────────────────────────────────────────┐")
    print("│ ffmpeg -i input.mp4 -i palette.png          │")
    print("│   -lavfi fps=15,scale=480:-1:flags=lanczos, │")
    print("│          [x];[x][1:v]paletteuse             │")
    print("│   output.gif                                │")
    print("└─────────────────────────────────────────────┘")

    print("\n第 3 步：使用 gifsicle 优化（可选）")
    print("┌─────────────────────────────────────────────┐")
    print("│ gifsicle -O3 --lossy=30 --colors=256        │")
    print("│   -o output_opt.gif output.gif              │")
    print("└─────────────────────────────────────────────┘")

    print("\n转换参数说明:")
    print("  - fps=15: 输出 GIF 帧率（降低帧率可减小文件大小）")
    print("  - scale=480:-1: 宽度 480px，高度自动计算")
    print("  - flags=lanczos: 使用 Lanczos 算法进行高质量缩放")
    print("  - palettegen: 生成自定义调色板")
    print("  - paletteuse: 使用自定义调色板应用颜色")
    print("  - --lossy=30: 有损压缩，损失 30% 质量（可选）")


async def demo_twitter_parsing():
    """演示推特解析流程"""
    print("\n" + "=" * 60)
    print("演示 3: 推特解析流程")
    print("=" * 60)

    print("\n方案 1：解析 JSON 中的 type 字段（首选）")
    print("┌─────────────────────────────────────────────┐")
    print("│ 1. 检查 HTML 中的 <script type='application/json'> 标签")
    print("│ 2. 搜索 '\"type\":\"animated_gif\"' 字符串")
    print("│ 3. 如果找到，标记为 GIF，下载后转换")
    print("└─────────────────────────────────────────────┘")

    print("\n方案 2：下载后使用 ffprobe 检测（备选）")
    print("┌─────────────────────────────────────────────┐")
    print("│ 1. 下载视频文件")
    print("│ 2. 使用 ffprobe 检测是否有音频流")
    print("│ 3. 无音频流 → 转换为 GIF")
    print("│ 4. 有音频流 → 保持原视频格式")
    print("└─────────────────────────────────────────────┘")

    print("\n当前实现：")
    print("  ✅ 两种方案都支持")
    print("  ✅ 优先使用 JSON type 字段检测")
    print("  ✅ 自动回退到 ffprobe 检测")


async def demo_config():
    """演示配置说明"""
    print("\n" + "=" * 60)
    print("演示 4: 配置说明")
    print("=" * 60)

    print("\n代理配置（可选）：")
    print("┌─────────────────────────────────────────────┐")
    print("│ # .env 文件                                  │")
    print("│ parser_proxy=http://localhost:xxxxx         │")
    print("└─────────────────────────────────────────────┘")

    print("\nffmpeg/ffprobe 要求：")
    print("  - ffmpeg 必须安装并在 PATH 中")
    print("  - ffprobe 必须安装并在 PATH 中")
    print("  - gifsicle 可选（用于进一步优化 GIF）")

    print("\n安装命令：")
    print("  Windows: choco install ffmpeg gifsicle")
    print("  Linux:   sudo apt install ffmpeg gifsicle")
    print("  macOS:   brew install ffmpeg gifsicle")


async def demo_usage():
    """演示使用方法"""
    print("\n" + "=" * 60)
    print("演示 5: 使用方法")
    print("=" * 60)

    print("\n在群聊中发送推特链接：")
    print("  ┌─────────────────────────────────────────┐")
    print("  │ https://x.com/i/status/2017344867878248543  │")
    print("  └─────────────────────────────────────────┘")

    print("\n处理流程：")
    print("  1. 机器人识别推特链接")
    print("  2. 调用 xdown.app API 获取信息")
    print("  3. 检测是否为 GIF（JSON type 或 ffprobe）")
    print("  4. 下载视频文件")
    print("  5. 如果是 GIF，转换为 .gif 格式")
    print("  6. 发送转换后的 GIF 给用户")

    print("\n测试链接：")
    print("  GIF: https://x.com/i/status/2017344867878248543")
    print("  视频: https://x.com/i/status/2017206656862658581")


async def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 18 + "推特 GIF 转换功能演示" + " " * 18 + "║")
    print("╚" + "=" * 58 + "╝")

    await demo_ffprobe()
    await demo_gif_conversion()
    await demo_twitter_parsing()
    await demo_config()
    await demo_usage()

    print("\n" + "=" * 60)
    print("功能特点")
    print("=" * 60)
    print("""
✅ 自动检测 GIF 内容（JSON type 字段优先）
✅ 使用 palettegen 滤镜生成高质量 GIF
✅ 备选方案：使用 ffprobe 检测音频流
✅ 支持代理配置
✅ 可选的 gifsicle 优化
✅ 异步处理，不阻塞机器人
""")

    print("=" * 60)
    print("注意事项")
    print("=" * 60)
    print("""
⚠️  ffmpeg 和 ffprobe 必须安装并在 PATH 中
⚠️  GIF 转换需要一定时间，请耐心等待
⚠️  转换后的 GIF 文件可能比原始视频大
⚠️  建议使用代理访问推特 API
⚠️  gifsicle 是可选的，但推荐安装
""")


if __name__ == "__main__":
    asyncio.run(main())
