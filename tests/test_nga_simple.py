"""独立测试 curl_cffi 下载 NGA 图片"""
import tempfile
from pathlib import Path

# 测试 URL
test_url = "https://img.nga.178.com/attachments/mon_202601/13/-zue37Q7eoy-6db6ZlT3cSdr-264.png"

print("=" * 60)
print("NGA 图片下载测试 (curl_cffi)")
print("=" * 60)
print(f"URL: {test_url}")

# 测试 curl_cffi 是否可用
try:
    from curl_cffi import requests as curl_requests
    print("\n✓ curl_cffi 已安装")
except ImportError:
    print("\n✗ curl_cffi 未安装")
    print("  请运行: pip install curl_cffi")
    exit(1)

# 创建临时目录
temp_dir = Path(tempfile.mkdtemp())
file_path = temp_dir / "test_nga_image.png"

print(f"保存路径: {file_path}")

try:
    # 使用 curl_cffi 下载
    print("\n开始下载...")
    headers = {
        "Referer": "https://bbs.nga.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    response = curl_requests.get(
        test_url,
        headers=headers,
        impersonate="chrome110",  # 模拟 Chrome 110 浏览器
        timeout=30,
    )

    response.raise_for_status()

    # 保存文件
    with open(file_path, "wb") as f:
        f.write(response.content)

    # 验证结果
    file_size = file_path.stat().st_size
    print(f"\n✓ 下载成功！")
    print(f"  文件大小: {file_size} 字节 ({file_size / 1024:.2f} KB)")

    # 检查是否是有效的图片
    with open(file_path, "rb") as f:
        header = f.read(8)
        if header.startswith(b'\x89PNG\r\n\x1a\n'):
            print(f"  ✓ 文件验证通过 (PNG)")
        elif header.startswith(b'\xff\xd8\xff'):
            print(f"  ✓ 文件验证通过 (JPEG)")
        else:
            print(f"  ✗ 文件头验证失败: {header.hex()}")

    # 对比 httpx
    print("\n" + "=" * 60)
    print("对比测试: httpx (不使用 curl_cffi)")
    print("=" * 60)

    import httpx
    httpx_path = temp_dir / "test_httpx.png"

    try:
        with httpx.Client() as client:
            response = client.get(
                test_url,
                headers=headers,
                follow_redirects=True,
                timeout=30,
            )
            response.raise_for_status()

        with open(httpx_path, "wb") as f:
            f.write(response.content)

        httpx_size = httpx_path.stat().st_size
        print(f"  httpx 下载大小: {httpx_size} 字节 ({httpx_size / 1024:.2f} KB)")

        # 检查是否有效
        with open(httpx_path, "rb") as f:
            header = f.read(8)
            if header.startswith(b'\x89PNG\r\n\x1a\n'):
                print(f"  ✓ httpx 文件验证通过 (PNG)")
            elif header.startswith(b'\xff\xd8\xff'):
                print(f"  ✓ httpx 文件验证通过 (JPEG)")
            else:
                print(f"  ✗ httpx 文件头验证失败: {header.hex()}")

        # 对比
        print(f"\n对比结果:")
        print(f"  curl_cffi: {file_size} 字节")
        print(f"  httpx:     {httpx_size} 字节")
        print(f"  差异:      {file_size - httpx_size} 字节")

    except httpx.HTTPError as e:
        print(f"  ✗ httpx 下载失败: {e}")

except Exception as e:
    print(f"\n✗ 下载失败: {e}")
    import traceback
    traceback.print_exc()

finally:
    # 清理
    import shutil
    try:
        shutil.rmtree(temp_dir)
        print(f"\n已清理临时目录")
    except:
        pass
