"""测试 NGA 图片下载功能"""
import asyncio
import tempfile
from pathlib import Path

# 设置临时缓存目录
temp_dir = tempfile.mkdtemp()
cache_dir = Path(temp_dir)

# 模拟配置
class MockConfig:
    cache_dir = cache_dir
    max_size = 100  # 100 MB

import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

# 修改 config 模块
import nonebot_plugin_parser.config as config_module
config_module.pconfig = MockConfig()

from nonebot_plugin_parser.download import DOWNLOADER


async def test_nga_download():
    """测试 NGA 图片下载"""
    test_url = "https://img.nga.178.com/attachments/mon_202601/13/-zue37Q7eoy-6db6ZlT3cSdr-264.png"

    print(f"开始测试 NGA 图片下载...")
    print(f"URL: {test_url}")
    print(f"缓存目录: {cache_dir}")
    print(f"curl_cffi 可用: {DOWNLOADER._download_with_curl_cffi is not None if hasattr(DOWNLOADER, '_download_with_curl_cffi') else 'No method'}")

    try:
        file_path = await DOWNLOADER.streamd(test_url)
        print(f"\n✓ 下载成功！")
        print(f"  文件路径: {file_path}")
        print(f"  文件大小: {file_path.stat().st_size} 字节 ({file_path.stat().st_size / 1024:.2f} KB)")

        # 验证文件是否是有效的 PNG 图片
        if file_path.exists() and file_path.stat().st_size > 0:
            print(f"  文件存在且有效")

            # 检查文件头
            with open(file_path, 'rb') as f:
                header = f.read(8)
                if header.startswith(b'\x89PNG\r\n\x1a\n'):
                    print(f"  ✓ 文件头验证通过 (PNG)")
                else:
                    print(f"  ✗ 文件头验证失败")
                    print(f"    文件头: {header.hex()}")
        else:
            print(f"✗ 文件不存在或大小为 0")

    except Exception as e:
        print(f"\n✗ 下载失败: {e}")
        import traceback
        traceback.print_exc()


async def test_regular_download():
    """测试普通图片下载（非 NGA）"""
    test_url = "https://httpbin.org/image/png"

    print(f"\n\n开始测试普通图片下载 (httpx)...")
    print(f"URL: {test_url}")

    try:
        file_path = await DOWNLOADER.streamd(test_url)
        print(f"\n✓ 下载成功！")
        print(f"  文件路径: {file_path}")
        print(f"  文件大小: {file_path.stat().st_size} 字节")
    except Exception as e:
        print(f"\n✗ 下载失败: {e}")


async def main():
    """主测试函数"""
    print("=" * 60)
    print("NGA 图片下载测试")
    print("=" * 60)

    # 检查 curl_cffi 是否可用
    try:
        from curl_cffi import requests as curl_requests
        print("✓ curl_cffi 已安装")
    except ImportError:
        print("✗ curl_cffi 未安装，将使用 httpx 回退")
        print("  请运行: pip install curl_cffi")

    await test_nga_download()
    await test_regular_download()

    # 清理
    import shutil
    try:
        shutil.rmtree(cache_dir)
        print(f"\n已清理临时目录: {cache_dir}")
    except:
        pass


if __name__ == "__main__":
    asyncio.run(main())
