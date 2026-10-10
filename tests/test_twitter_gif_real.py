"""
推特 GIF 转换实际测试

测试项目：
1. 实际请求推特链接
2. 检测是否为 GIF（JSON type 字段）
3. 下载视频文件
4. 检测音频流（ffprobe）
5. 转换为 GIF
6. 显示文件信息对比

测试链接：
- GIF: https://x.com/i/status/2017344867878248543
- 视频: https://x.com/i/status/2017206656862658581
"""

import asyncio
import json
import tempfile
from pathlib import Path
from datetime import datetime

import httpx
from bs4 import BeautifulSoup


# 配置
PROXY = "http://localhost:17890"
TIMEOUT = 30.0


async def test_twitter_gif_detection():
    """测试推特 GIF 检测功能"""
    print("=" * 70)
    print("测试 1: 推特 GIF 检测（JSON type 字段）")
    print("=" * 70)

    test_urls = [
        ("GIF", "https://x.com/i/status/2017344867878248543"),
        ("视频", "https://x.com/i/status/2017206656862658581"),
    ]

    for label, url in test_urls:
        print(f"\n{'─' * 70}")
        print(f"测试: {label} - {url}")
        print(f"{'─' * 70}")

        try:
            # 调用 xdown.app API
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://xdown.app",
                "Referer": "https://xdown.app/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            data = {"q": url, "lang": "zh-cn"}

            # httpx 代理配置
            proxy = PROXY if PROXY else None
            async with httpx.AsyncClient(timeout=TIMEOUT, proxy=proxy) as client:
                response = await client.post(
                    "https://xdown.app/api/ajaxSearch",
                    headers=headers,
                    data=data,
                )
                result = response.json()

            if result.get("status") != "ok":
                print(f"❌ API 请求失败: {result.get('msg', '未知错误')}")
                continue

            html_content = result.get("data")
            if not html_content:
                print(f"❌ HTML 内容为空")
                continue

            # 解析 HTML，检测 type 字段
            soup = BeautifulSoup(html_content, "html.parser")

            # 检查 JSON 中的 type 字段
            script_tags = soup.find_all("script", type="application/json")
            is_animated_gif = False
            found_type = False

            for script_tag in script_tags:
                script_text = script_tag.get_text()
                if '"type"' in script_text:
                    found_type = True
                    if '"type":"animated_gif"' in script_text or '"type": "animated_gif"' in script_text:
                        is_animated_gif = True
                        break

            # 检查下载链接
            dynamic_urls = []
            video_urls = []

            for tag in soup.find_all("a", class_=["tw-button-dl", "abutton"]):
                href = tag.get("href")
                text = tag.get_text(strip=True)
                if href and "下载 gif" in text:
                    dynamic_urls.append(href)
                elif href and "下载 MP4" in text:
                    video_urls.append(href)

            # 输出结果
            print(f"✅ API 请求成功")
            print(f"  检测到 type 字段: {'是' if found_type else '否'}")
            print(f"  type=animated_gif: {'是' if is_animated_gif else '否'}")
            print(f"  GIF 下载链接: {len(dynamic_urls)} 个")
            print(f"  视频下载链接: {len(video_urls)} 个")

            if dynamic_urls:
                print(f"  第一个 GIF URL: {dynamic_urls[0][:80]}...")

            # 预期结果对比
            expected = "GIF" if label == "GIF" else "视频"
            actual = "GIF" if is_animated_gif else "视频"
            match = "✅" if expected == actual else "❌"
            print(f"\n  {match} 预期: {expected}, 实际: {actual}")

        except Exception as e:
            print(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()


async def test_download_and_convert():
    """测试下载和 GIF 转换功能"""
    print("\n\n" + "=" * 70)
    print("测试 2: 下载和 GIF 转换")
    print("=" * 70)

    # 使用一个简单的 GIF 视频测试 URL
    test_url = "https://img.nga.cn/attachments/mon_202601/13/-zue37Q7eoy-6db6ZlT3cSdr-264.png"
    # 使用推特 GIF
    test_url = "https://x.com/i/status/2017344867878248543"

    print(f"\n测试 URL: {test_url}")
    print(f"代理: {PROXY if PROXY else '无'}")

    temp_dir = Path(tempfile.mkdtemp())
    print(f"临时目录: {temp_dir}")

    try:
        # 步骤 1: 获取下载链接
        print(f"\n{'─' * 70}")
        print("步骤 1: 获取推特下载链接")
        print(f"{'─' * 70}")

        proxy = PROXY if PROXY else None
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://xdown.app",
            "Referer": "https://xdown.app/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        data = {"q": test_url, "lang": "zh-cn"}

        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=proxy) as client:
            response = await client.post(
                "https://xdown.app/api/ajaxSearch",
                headers=headers,
                data=data,
            )
            result = response.json()

        if result.get("status") != "ok":
            print(f"❌ API 请求失败")
            return

        html_content = result.get("data")
        soup = BeautifulSoup(html_content, "html.parser")

        # 获取 GIF 下载链接
        dynamic_url = None
        for tag in soup.find_all("a", class_=["tw-button-dl", "abutton"]):
            href = tag.get("href")
            text = tag.get_text(strip=True)
            if href and "下载 gif" in text:
                dynamic_url = href
                break

        if not dynamic_url:
            print(f"❌ 未找到 GIF 下载链接")
            # 尝试获取视频链接
            for tag in soup.find_all("a", class_=["tw-button-dl", "abutton"]):
                href = tag.get("href")
                text = tag.get_text(strip=True)
                if href and "下载 MP4" in text:
                    dynamic_url = href
                    print(f"⚠️  未找到 GIF 链接，使用视频链接测试")
                    break

        if not dynamic_url:
            print(f"❌ 未找到任何下载链接")
            return

        print(f"✅ 找到下载链接: {dynamic_url[:80]}...")

        # 步骤 2: 下载视频
        print(f"\n{'─' * 70}")
        print("步骤 2: 下载视频文件")
        print(f"{'─' * 70}")

        video_path = temp_dir / "video.mp4"

        proxy = PROXY if PROXY else None
        async with httpx.AsyncClient(timeout=TIMEOUT, proxy=proxy) as client:
            async with client.stream("GET", dynamic_url, headers=headers, follow_redirects=True) as response:
                response.raise_for_status()
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                print(f"  开始下载...")
                print(f"  文件大小: {total_size / 1024:.2f} KB")

                with open(video_path, "wb") as f:
                    async for chunk in response.aiter_bytes(8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = downloaded / total_size * 100
                            print(f"\r  进度: {progress:.1f}% ({downloaded / 1024:.1f} KB)", end="")

        print(f"\n✅ 下载完成: {video_path}")

        # 步骤 3: 检测音频流
        print(f"\n{'─' * 70}")
        print("步骤 3: 检测音频流（ffprobe）")
        print(f"{'─' * 70}")

        has_audio = await check_audio_stream(video_path)
        print(f"  检测结果: {'🔊 有音频流（普通视频）' if has_audio else '🔇 无音频流（GIF 视频）'}")

        if has_audio:
            print(f"⚠️  检测到音频流，这是普通视频，跳过 GIF 转换")
            return

        # 步骤 4: 转换为 GIF
        print(f"\n{'─' * 70}")
        print("步骤 4: 转换为 GIF（palettegen）")
        print(f"{'─' * 70}")

        gif_path = await convert_to_gif(video_path, temp_dir / "output.gif")
        print(f"✅ GIF 转换完成: {gif_path}")

        # 步骤 5: 文件信息对比
        print(f"\n{'─' * 70}")
        print("步骤 5: 文件信息对比")
        print(f"{'─' * 70}")

        video_info = get_file_info(video_path)
        gif_info = get_file_info(gif_path)

        print(f"\n原始视频 (MP4):")
        for key, value in video_info.items():
            print(f"  {key}: {value}")

        print(f"\n转换后 (GIF):")
        for key, value in gif_info.items():
            print(f"  {key}: {value}")

        # 对比
        print(f"\n文件大小变化:")
        size_change = gif_info['size_bytes'] - video_info['size_bytes']
        size_percent = (gif_info['size_bytes'] / video_info['size_bytes'] - 1) * 100
        if size_change > 0:
            print(f"  ⚠️  GIF 大了 {abs(size_change) / 1024:.1f} KB ({size_percent:+.1f}%)")
        else:
            print(f"  ✅ GIF 小了 {abs(size_change) / 1024:.1f} KB ({size_percent:+.1f}%)")

        print(f"\n{'=' * 70}")
        print("✅ 测试完成！所有步骤都成功执行")
        print(f"{'=' * 70}")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 清理临时文件
        try:
            import shutil
            shutil.rmtree(temp_dir)
            print(f"\n已清理临时目录: {temp_dir}")
        except:
            pass


async def check_audio_stream(video_path: Path) -> bool:
    """检测视频是否包含音频流"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(video_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode()
            print(f"  ⚠️  ffprobe 错误: {error[:200]}")
            return False

        output = stdout.decode().strip()
        return bool(output)
    except FileNotFoundError:
        print(f"  ❌ ffprobe 未安装")
        return False
    except Exception as e:
        print(f"  ❌ 检测失败: {e}")
        return False


async def convert_to_gif(video_path: Path, output_path: Path) -> Path:
    """转换视频为 GIF"""
    palette_path = video_path.with_name("palette.png")

    # 步骤 1: 生成调色板
    print(f"  步骤 1: 生成调色板...")
    palette_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", "fps=15,scale=480:-1:flags=lanczos,palettegen",
        str(palette_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *palette_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode()
        raise RuntimeError(f"调色板生成失败: {error[:200]}")

    # 步骤 2: 生成 GIF
    print(f"  步骤 2: 生成 GIF...")
    gif_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(palette_path),
        "-lavfi", "fps=15,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse",
        str(output_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *gif_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode()
        raise RuntimeError(f"GIF 生成失败: {error[:200]}")

    # 清理调色板
    try:
        palette_path.unlink()
    except:
        pass

    return output_path


def get_file_info(file_path: Path) -> dict:
    """获取文件信息"""
    stat = file_path.stat()

    info = {
        "文件名": file_path.name,
        "格式": file_path.suffix.upper(),
        "size_bytes": stat.st_size,
        "文件大小": f"{stat.st_size / 1024:.2f} KB",
    }

    # 检测视频信息
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size:stream=width,height,r_frame_rate",
             "-of", "json", str(file_path)],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            format_info = data.get("format", {})
            streams = data.get("streams", [])

            if streams:
                stream = streams[0]
                info["分辨率"] = f"{stream.get('width', '?')}x{stream.get('height', '?')}"
                info["帧率"] = stream.get('r_frame_rate', '?').split('/')[0] if '/' in str(stream.get('r_frame_rate', '')) else stream.get('r_frame_rate', '?')

            duration = float(format_info.get('duration', 0))
            if duration > 0:
                info["时长"] = f"{duration:.2f} 秒"

    except Exception as e:
        info["备注"] = f"无法获取详细信息: {e}"

    return info


async def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 18 + "推特 GIF 转换实际测试" + " " * 18 + "║")
    print("╚" + "=" * 68 + "╝")

    print(f"\n开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"代理设置: {PROXY}")

    # 检查工具
    print(f"\n{'=' * 70}")
    print("检查必需工具")
    print(f"{'=' * 70}")

    import shutil
    tools = {
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
    }

    for name, cmd in tools.items():
        available = shutil.which(cmd) is not None
        status = "✅" if available else "❌"
        print(f"  {status} {name}")

    missing = [name for name, cmd in tools.items() if shutil.which(cmd) is None]
    if missing:
        print(f"\n❌ 缺少必需工具: {', '.join(missing)}")
        print(f"请安装: {' '.join(missing)}")
        return

    # 运行测试
    await test_twitter_gif_detection()
    await test_download_and_convert()

    print(f"\n结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
