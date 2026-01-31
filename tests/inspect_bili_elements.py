"""
检查 B 站动态和图文页面的 DOM 结构
用于确定正确的 CSS 选择器
"""
import asyncio
import re
from playwright.async_api import async_playwright

def extract_id(url: str) -> tuple[str, int]:
    """从 URL 中提取类型和 ID"""
    if "/opus/" in url:
        return "opus", int(re.search(r"/opus/(\d+)", url).group(1))
    elif "/dynamic/" in url:
        return "dynamic", int(re.search(r"/dynamic/(\d+)", url).group(1))
    elif "t.bilibili.com/" in url:
        return "dynamic", int(re.search(r"t\.bilibili\.com/(\d+)", url).group(1))
    else:
        raise ValueError(f"无法识别的 URL: {url}")


async def inspect_page(url: str, output_prefix: str):
    """检查单个页面的元素结构"""
    print(f"\n{'='*60}")
    print(f"检查 URL: {url}")
    print(f"{'='*60}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 414, "height": 800},
            is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )

        page = await context.new_page()

        print(f"访问 URL...")
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"访问失败: {e}")
            await browser.close()
            return

        # 等待内容加载
        await asyncio.sleep(3)

        # 检查主要容器是否存在
        selectors_to_check = [
            # 新版 Opus 相关
            ".opus-modules",
            ".opus-detail",
            ".opus-container",
            ".opus-content",
            # 旧版 Dynamic 相关
            ".dyn-card",
            ".dyn-content",
            ".dynamic-detail",
            # 通用容器
            ".card",
            ".main-content",
            "#app",
            "body > div",
        ]

        print(f"\n=== 选择器检查 ===")
        found_selectors = []
        for selector in selectors_to_check:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    element = page.locator(selector).first
                    is_visible = await element.is_visible()
                    bbox = await element.bounding_box() if is_visible else None

                    if is_visible and bbox:
                        found_selectors.append(selector)
                        print(f"✓ {selector}:")
                        print(f"  - 数量: {count}")
                        print(f"  - 位置: x={bbox['x']}, y={bbox['y']}, width={bbox['width']}, height={bbox['height']}")

                        # 获取元素的类名和 ID
                        class_attr = await element.get_attribute("class") or ""
                        id_attr = await element.get_attribute("id") or ""
                        print(f"  - class='{class_attr}' id='{id_attr}'")
            except Exception as e:
                pass

        if not found_selectors:
            print("未找到任何可见的容器元素")

        # 获取 body 的直接子元素
        print(f"\n=== Body 直接子元素 ===")
        children = await page.evaluate("""() => {
            const children = Array.from(document.body.children);
            return children.map((child, idx) => {
                const style = window.getComputedStyle(child);
                return {
                    index: idx,
                    tagName: child.tagName,
                    className: child.className,
                    id: child.id,
                    visible: style.display !== 'none',
                    height: child.offsetHeight,
                    width: child.offsetWidth
                };
            });
        }""")

        visible_children = [c for c in children if c['visible']]
        print(f"总共 {len(children)} 个子元素，{len(visible_children)} 个可见")

        for child in visible_children[:15]:  # 只看前 15 个
            print(f"  {child['tagName']}:")
            print(f"    class='{child['className'][:80]}'")
            print(f"    id='{child['id']}'")
            print(f"    size={child['width']}x{child['height']}")

        # 检查页面高度
        page_height = await page.evaluate("() => document.body.scrollHeight")
        viewport_height = await page.evaluate("() => window.innerHeight")
        print(f"\n=== 页面尺寸 ===")
        print(f"页面高度: {page_height}px")
        print(f"视口高度: {viewport_height}px")

        # 截图保存
        screenshot_path = f"{output_prefix}.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        print(f"\n截图已保存到: {screenshot_path}")

        # 保存 HTML
        html_content = await page.content()
        html_path = f"{output_prefix}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"HTML 已保存到: {html_path}")

        await browser.close()


async def main():
    # 测试链接
    test_urls = [
        ("https://m.bilibili.com/opus/1159504791855955984", "test_opus_1159504791855955984"),
        ("https://t.bilibili.com/1156587796127809560", "test_dynamic_1156587796127809560_t"),
        ("https://www.bilibili.com/opus/1156587796127809560", "test_dynamic_1156587796127809560_www"),
    ]

    for url, prefix in test_urls:
        try:
            await inspect_page(url, prefix)
        except Exception as e:
            print(f"\n处理 {url} 时出错: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
