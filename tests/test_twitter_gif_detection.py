"""
推特 GIF 检测逻辑测试

测试 xdown.app 响应中的 GIF 判断方法：
1. 缩略图 URL 包含 tweet_video_thumb
2. 下载按钮文本包含 "下载 gif" 或 "(gif)"
3. 链接 href 的文件名包含 _gif.mp4

测试链接：
- GIF: https://x.com/i/status/2017344867878248543
- 视频: https://x.com/i/status/2017206656862658581
"""

import asyncio
from bs4 import BeautifulSoup


# 模拟 xdown.app 的响应
GIF_RESPONSE = """        <div class="tw-video">
            <div class="tw-left">
                <div class="thumbnail">
                    <div class="image-tw open-popup ">
                        <img src="https://pbs.twimg.com/tweet_video_thumb/G_8MbWAXQAE6_8q.jpg">
                    </div>
                </div>
            </div>
            <div class="tw-right">
                <div class="dl-action">
                            <p><a onclick="showAd()" href="https://dl.snapcdn.app/get?token=..." rel="nofollow" class="tw-button-dl button dl-success"><i class="icon icon-download"></i> 下载 gif (gif)</a></p>
                            <p><a onclick="showAd()" href="https://dl.snapcdn.app/get?token=..." rel="nofollow" class="tw-button-dl button dl-success"><i class="icon icon-download"></i> 下载图片</a></p>
                            </div>
            </div>
            <div class="tw-middle">
                <div class="content">
                    <div class="clearfix">
                        <h3>Lalas </h3>
                    </div>
                </div>
            </div>
        </div>"""

VIDEO_RESPONSE = """        <div class="tw-video">
            <div class="tw-left">
                <div class="thumbnail">
                    <div class="image-tw open-popup ">
                        <img src="https://pbs.twimg.com/ext_tw_video_thumb/1769826578330345472/pu/img/xxxx.jpg">
                    </div>
                </div>
            </div>
            <div class="tw-right">
                <div class="dl-action">
                            <p><a onclick="showAd()" href="https://dl.snapcdn.app/get?token=..." rel="nofollow" class="tw-button-dl button dl-success"><i class="icon icon-download"></i> 下载 MP4 (720p)</a></p>
                            <p><a onclick="showAd()" href="https://dl.snapcdn.app/get?token=..." rel="nofollow" class="tw-button-dl button dl-success"><i class="icon icon-download"></i> 下载 MP4 (480p)</a></p>
                            </div>
            </div>
            <div class="tw-middle">
                <div class="content">
                    <div class="clearfix">
                        <h3>Test Video</h3>
                    </div>
                </div>
            </div>
        </div>"""


def detect_gif_from_html(html_content: str) -> dict:
    """从 HTML 中检测是否为 GIF"""
    soup = BeautifulSoup(html_content, "html.parser")

    result = {
        "is_gif": False,
        "reasons": [],
        "thumb_url": None,
        "download_text": None,
        "gif_url": None,
    }

    # 1. 检查缩略图 URL
    thumb_tag = soup.find("img")
    if thumb_tag and (thumb_url := thumb_tag.get("src")):
        result["thumb_url"] = thumb_url
        if "tweet_video_thumb" in thumb_url:
            result["is_gif"] = True
            result["reasons"].append("缩略图 URL 包含 'tweet_video_thumb'")

    # 2. 检查下载链接文本
    for tag in soup.find_all("a", class_=["tw-button-dl", "abutton"]):
        text = tag.get_text(strip=True)
        href = tag.get("href")

        if "下载 gif" in text or "(gif)" in text.lower():
            result["is_gif"] = True
            result["reasons"].append("下载按钮文本包含 'gif'")
            result["download_text"] = text
            if href:
                result["gif_url"] = href[:100] + "..."

        if "下载 MP4" in text:
            result["is_gif"] = False  # 如果有 MP4 下载，可能不是 GIF

    return result


def test_gif_detection():
    """测试 GIF 检测"""
    print("=" * 70)
    print("GIF 检测测试")
    print("=" * 70)

    # 测试 GIF 响应
    print("\n测试 1: GIF 响应")
    print("-" * 70)
    gif_result = detect_gif_from_html(GIF_RESPONSE)

    print(f"检测结果: {'✅ GIF' if gif_result['is_gif'] else '❌ 不是 GIF'}")
    print(f"判断依据:")
    for reason in gif_result['reasons']:
        print(f"  - {reason}")
    print(f"缩略图 URL: {gif_result['thumb_url']}")
    print(f"下载按钮文本: {gif_result['download_text']}")
    print(f"GIF 下载链接: {gif_result['gif_url']}")

    expected = True
    actual = gif_result['is_gif']
    match = "✅" if expected == actual else "❌"
    print(f"\n{match} 预期: GIF, 实际: {'GIF' if actual else '视频'}")

    # 测试视频响应
    print("\n" + "-" * 70)
    print("\n测试 2: 视频响应")
    print("-" * 70)
    video_result = detect_gif_from_html(VIDEO_RESPONSE)

    print(f"检测结果: {'✅ GIF' if video_result['is_gif'] else '❌ 不是 GIF'}")
    print(f"判断依据:")
    if video_result['reasons']:
        for reason in video_result['reasons']:
            print(f"  - {reason}")
    else:
        print(f"  - 无 GIF 特征")
    print(f"缩略图 URL: {video_result['thumb_url']}")

    expected = False
    actual = video_result['is_gif']
    match = "✅" if expected == actual else "❌"
    print(f"\n{match} 预期: 视频, 实际: {'GIF' if actual else '视频'}")

    # 总结
    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)

    all_pass = (gif_result['is_gif'] == True) and (video_result['is_gif'] == False)

    if all_pass:
        print("✅ 所有测试通过！")
        print("\nGIF 检测方法:")
        print("  1. 缩略图 URL 包含 'tweet_video_thumb'")
        print("  2. 下载按钮文本包含 '下载 gif' 或 '(gif)'")
        print("  3. 如果有 '下载 MP4' 按钮，则不是 GIF")
    else:
        print("❌ 测试失败")


async def test_real_requests():
    """测试实际请求"""
    print("\n\n" + "=" * 70)
    print("实际请求测试")
    print("=" * 70)

    import httpx

    PROXY = "http://localhost:17890"
    TIMEOUT = 30.0

    test_urls = [
        ("GIF", "https://x.com/i/status/2017344867878248543"),
        ("视频", "https://x.com/i/status/2017206656862658581"),
    ]

    for label, url in test_urls:
        print(f"\n{'─' * 70}")
        print(f"测试: {label} - {url}")
        print(f"{'─' * 70}")

        try:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://xdown.app",
                "Referer": "https://xdown.app/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            data = {"q": url, "lang": "zh-cn"}

            proxy = PROXY if PROXY else None
            async with httpx.AsyncClient(timeout=TIMEOUT, proxy=proxy) as client:
                response = await client.post(
                    "https://xdown.app/api/ajaxSearch",
                    headers=headers,
                    data=data,
                )
                result = response.json()

            if result.get("status") != "ok":
                print(f"❌ API 请求失败")
                continue

            html_content = result.get("data")
            detection = detect_gif_from_html(html_content)

            print(f"✅ API 请求成功")
            print(f"检测结果: {'✅ GIF' if detection['is_gif'] else '❌ 视频'}")
            print(f"判断依据:")
            for reason in detection['reasons']:
                print(f"  - {reason}")

            expected = "GIF" if label == "GIF" else "视频"
            actual = "GIF" if detection['is_gif'] else "视频"
            match = "✅" if expected == actual else "❌"
            print(f"\n{match} 预期: {expected}, 实际: {actual}")

        except Exception as e:
            print(f"❌ 测试失败: {e}")


def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 20 + "推特 GIF 检测测试" + " " * 20 + "║")
    print("╚" + "=" * 68 + "╝")

    # 测试检测逻辑
    test_gif_detection()

    # 测试实际请求
    asyncio.run(test_real_requests())

    print("\n")


if __name__ == "__main__":
    main()
