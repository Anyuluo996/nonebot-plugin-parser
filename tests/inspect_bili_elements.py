"""
检查 B 站动态和图文页面的 DOM 结构
用于确定正确的 CSS 选择器
"""
import asyncio
from playwright.async_api import async_playwright

async def inspect_dynamic_page(dynamic_id: int):
    """检查动态页面的元素结构"""
    print(f"\n=== 检查动态页面 {dynamic_id} ===")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 414, "height": 800},
            is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )

        page = await context.new_page()
        url = f"https://m.bilibili.com/dynamic/{dynamic_id}"

        print(f"访问 URL: {url}")
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # 等待内容加载
        await asyncio.sleep(3)

        # 检查主要容器是否存在
        selectors_to_check = [
            ".opus-modules",
            ".dyn-card",
            ".opus-detail",
            ".dyn-content",
            ".card",
            ".dynamic-detail",
            ".main-content",
            "#app",
            "body > div",
        ]

        print("\n=== 检查选择器 ===")
        for selector in selectors_to_check:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    element = page.locator(selector).first
                    is_visible = await element.is_visible()
                    bbox = await element.bounding_box() if is_visible else None

                    print(f"✓ {selector}:")
                    print(f"  - 数量: {count}")
                    print(f"  - 可见: {is_visible}")
                    if bbox:
                        print(f"  - 位置: x={bbox['x']}, y={bbox['y']}, width={bbox['width']}, height={bbox['height']}")

                    # 获取元素的 HTML 结构 (前 500 字符)
                    html = await element.inner_html()
                    print(f"  - HTML 预览: {html[:200]}...")
            except Exception as e:
                print(f"✗ {selector}: {e}")

        # 获取页面所有顶级 div
        print("\n=== 页面顶级结构 ===")
        top_divs = await page.locator("body > div").all()
        for i, div in enumerate(top_divs[:10]):  # 只看前 10 个
            try:
                class_name = await div.get_attribute("class")
                id_name = await div.get_attribute("id")
                is_visible = await div.is_visible()
                print(f"{i+1}. class='{class_name}' id='{id_name}' visible={is_visible}")
            except:
                pass

        # 获取 body 的直接子元素
        print("\n=== body 直接子元素 ===")
        children = await page.evaluate("""() => {
            const children = Array.from(document.body.children);
            return children.map((child, idx) => ({
                index: idx,
                tagName: child.tagName,
                className: child.className,
                id: child.id,
                visible: window.getComputedStyle(child).display !== 'none',
                height: child.offsetHeight
            }));
        }""")

        for child in children:
            if child['visible']:
                print(f"  {child['tagName']}: class='{child['className'][:50]}' id='{child['id']}' height={child['height']}")

        # 截图保存
        await page.screenshot(path=f"dynamic_{dynamic_id}.png", full_page=True)
        print(f"\n截图已保存到: dynamic_{dynamic_id}.png")

        # 保存 HTML
        html_content = await page.content()
        with open(f"dynamic_{dynamic_id}.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"HTML 已保存到: dynamic_{dynamic_id}.html")

        await browser.close()


async def inspect_opus_page(opus_id: int):
    """检查图文页面的元素结构"""
    print(f"\n=== 检查图文页面 {opus_id} ===")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 414, "height": 800},
            is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        )

        page = await context.new_page()
        url = f"https://m.bilibili.com/opus/{opus_id}"

        print(f"访问 URL: {url}")
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # 等待内容加载
        await asyncio.sleep(3)

        # 检查主要容器是否存在
        selectors_to_check = [
            ".opus-modules",
            ".dyn-card",
            ".opus-detail",
            ".opus-content",
            ".card",
            ".opus-container",
            "#app",
            "body > div",
        ]

        print("\n=== 检查选择器 ===")
        for selector in selectors_to_check:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    element = page.locator(selector).first
                    is_visible = await element.is_visible()
                    bbox = await element.bounding_box() if is_visible else None

                    print(f"✓ {selector}:")
                    print(f"  - 数量: {count}")
                    print(f"  - 可见: {is_visible}")
                    if bbox:
                        print(f"  - 位置: x={bbox['x']}, y={bbox['y']}, width={bbox['width']}, height={bbox['height']}")

                    # 获取元素的 HTML 结构
                    html = await element.inner_html()
                    print(f"  - HTML 预览: {html[:200]}...")
            except Exception as e:
                print(f"✗ {selector}: {e}")

        # 获取页面所有顶级 div
        print("\n=== 页面顶级结构 ===")
        top_divs = await page.locator("body > div").all()
        for i, div in enumerate(top_divs[:10]):
            try:
                class_name = await div.get_attribute("class")
                id_name = await div.get_attribute("id")
                is_visible = await div.is_visible()
                print(f"{i+1}. class='{class_name}' id='{id_name}' visible={is_visible}")
            except:
                pass

        # 截图保存
        await page.screenshot(path=f"opus_{opus_id}.png", full_page=True)
        print(f"\n截图已保存到: opus_{opus_id}.png")

        # 保存 HTML
        html_content = await page.content()
        with open(f"opus_{opus_id}.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"HTML 已保存到: opus_{opus_id}.html")

        await browser.close()


async def main():
    # 测试动态 ID 和图文 ID
    # 你可以替换成实际的 ID

    # 示例：使用你之前提供的 opus ID
    opus_id = 1159504791855955984

    # 同时测试动态格式 (opus 有时也会被识别为 dynamic)
    await inspect_opus_page(opus_id)
    await inspect_dynamic_page(opus_id)


if __name__ == "__main__":
    asyncio.run(main())
