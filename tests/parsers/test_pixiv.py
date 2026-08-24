import os

import pytest
from nonebot import logger

# Windows 终端编码问题，禁用 Rich 进度条输出
os.environ["FORCE_COLOR"] = "0"
os.environ["TERM"] = "dumb"


@pytest.mark.asyncio
async def test_pixiv_url_pattern():
    """测试 Pixiv URL 匹配"""
    from nonebot_plugin_parser.parsers import PixivParser

    parser = PixivParser()

    urls = [
        "https://www.pixiv.net/artworks/143155116",
        "https://pixiv.net/artworks/143155116",
        "https://www.pixiv.net/en/artworks/143155116",
    ]

    for url in urls:
        keyword, searched = parser.search_url(url)
        assert searched, f"无法匹配 URL: {url}"
        assert searched.group(1) == "143155116", f"无法提取 ID: {url}"
        logger.info(f"URL 匹配成功: {url} -> keyword={keyword}, id={searched.group(1)}")


@pytest.mark.flaky(reruns=3, reruns_delay=2)
@pytest.mark.asyncio
async def test_pixiv_parse():
    """测试 Pixiv 插画解析"""
    from nonebot_plugin_parser.parsers import PixivParser

    parser = PixivParser()

    url = "https://www.pixiv.net/artworks/143155116"
    keyword, searched = parser.search_url(url)
    assert searched, "无法匹配 URL"

    logger.info(f"{url} | 开始解析 Pixiv 插画")
    result = await parser.parse(keyword, searched)
    logger.debug(f"{url} | 解析结果: \n{result}")

    # 验证基本信息
    assert result.title, "标题为空"
    logger.info(f"标题: {result.title}")

    # 验证作者
    assert result.author, "作者信息为空"
    logger.info(f"作者: {result.author.name}")

    # 验证图片内容
    img_contents = result.img_contents
    assert img_contents, "图片内容为空"
    logger.info(f"图片数量: {len(img_contents)}")

    # 下载图片并验证
    for img_content in img_contents:
        path = await img_content.get_path()
        assert path.exists(), f"图片不存在: {path}"
        file_size = path.stat().st_size
        assert file_size > 0, f"图片文件为空: {path}"
        logger.info(f"图片下载成功: {path.name} ({file_size / 1024:.1f} KB)")

    # 验证 URL
    assert result.url, "来源链接为空"
    assert "143155116" in result.url, f"来源链接不正确: {result.url}"
    logger.info(f"来源链接: {result.url}")

    # 验证文本内容（简介）
    assert result.text, "简介文本为空"
    logger.info(f"简介: {result.text[:100]}...")


@pytest.mark.asyncio
async def test_pixiv_ugoira_parse():
    """测试 Pixiv 动图 (Ugoira) 解析"""
    from nonebot_plugin_parser.parsers import PixivParser

    parser = PixivParser()

    url = "https://www.pixiv.net/artworks/143137108"
    keyword, searched = parser.search_url(url)
    assert searched, "无法匹配 URL"

    logger.info(f"{url} | 开始解析 Pixiv 动图")
    result = await parser.parse(keyword, searched)
    logger.debug(f"{url} | 解析结果: \n{result}")

    # 验证基本信息
    assert result.title, "标题为空"
    logger.info(f"标题: {result.title}")

    # 验证作者
    assert result.author, "作者信息为空"
    logger.info(f"作者: {result.author.name}")

    # 验证动图内容
    dyn_contents = result.dynamic_contents
    assert dyn_contents, "动图内容为空"
    logger.info(f"动图内容数量: {len(dyn_contents)}")

    # 下载并验证
    for dyn_content in dyn_contents:
        path = await dyn_content.get_path()
        assert path.exists(), f"动图 ZIP 不存在: {path}"
        file_size = path.stat().st_size
        assert file_size > 0, f"动图 ZIP 文件为空: {path}"
        logger.info(f"动图 ZIP 下载成功: {path.name} ({file_size / 1024 / 1024:.1f} MB)")

        # 验证 GIF 转换
        gif_path = await dyn_content.get_gif_path()
        assert gif_path is not None, "GIF 路径为空"
        assert gif_path.exists(), f"GIF 文件不存在: {gif_path}"
        gif_size = gif_path.stat().st_size
        assert gif_size > 0, f"GIF 文件为空: {gif_path}"
        logger.info(f"动图 GIF 生成成功: {gif_path.name} ({gif_size / 1024:.1f} KB)")

    # 验证 GIF 首帧缩略图提取
    for dyn_content in dyn_contents:
        thumb = await dyn_content.get_thumbnail_path()
        assert thumb is not None, "缩略图路径为空"
        assert thumb.exists(), f"缩略图不存在: {thumb}"
        thumb_size = thumb.stat().st_size
        assert thumb_size > 0, f"缩略图为空: {thumb}"
        logger.info(f"动图缩略图提取成功: {thumb.name} ({thumb_size / 1024:.1f} KB)")

    # 验证 URL
    assert result.url, "来源链接为空"
    assert "143137108" in result.url, f"来源链接不正确: {result.url}"
    logger.info(f"来源链接: {result.url}")


@pytest.mark.asyncio
async def test_pixiv_render(tmp_path):
    """测试 Pixiv 图片渲染"""
    from nonebot_plugin_parser.parsers import PixivParser
    from nonebot_plugin_parser.renders import get_renderer

    parser = PixivParser()

    url = "https://www.pixiv.net/artworks/143155116"
    keyword, searched = parser.search_url(url)
    result = await parser.parse(keyword, searched)

    # 等待下载完成
    await result.ensure_downloads_complete()

    # 渲染图片（使用已初始化的 renderer singleton）
    renderer = get_renderer(result.platform.name)
    png_bytes = await renderer.render_image(result)

    out_path = tmp_path / "test_render_pixiv.png"
    out_path.write_bytes(png_bytes)
    logger.info(f"渲染图片已保存: {out_path} ({len(png_bytes) / 1024:.1f} KB)")


@pytest.mark.asyncio
async def test_pixiv_html_render(tmp_path):
    """测试 Pixiv HTML 渲染（需安装 nonebot-plugin-htmlrender + chromium 二进制）

    注意: 此测试实为渲染/像素测试, 但放在 tests/parsers/ 下。CI 的 Test Parsers job
    只装 python 包 (uv sync) 不装 chromium 二进制 (playwright install), 而本测试调用
    render_template → playwright 必须有 chromium 才能跑。
    故 skip 需同时挡 python 包 import 和 chromium 二进制存在, 不能只 try import。
    """
    from pathlib import Path

    from nonebot_plugin_parser.parsers import PixivParser

    try:
        from playwright.async_api import async_playwright
    except Exception:
        pytest.skip("playwright not available")

    try:
        from nonebot_plugin_htmlrender import render_template
    except Exception:
        pytest.skip("nonebot_plugin_htmlrender not available")

    # chromium 二进制是 lazy 安装 (playwright install), uv sync 不会装。
    # 仅 import 成功不代表浏览器可用; 必须检测 executable_path 实际存在。
    import os

    async with async_playwright() as p:
        if not p.chromium.executable_path or not os.path.exists(p.chromium.executable_path):
            pytest.skip("chromium binary not installed (run: playwright install chromium)")

    from nonebot_plugin_parser.renders import resources
    from nonebot_plugin_parser.renders.base import pconfig

    parser = PixivParser()

    url = "https://www.pixiv.net/artworks/143155116"
    keyword, searched = parser.search_url(url)
    result = await parser.parse(keyword, searched)

    # 等待所有下载完成（包括头像）
    await result.ensure_downloads_complete()

    logo = resources.RESOURCES_DIR / f"{result.platform.name}.png"
    logo_path = logo.as_uri() if logo.exists() else None
    font = pconfig.custom_font or resources.DEFAULT_FONT_PATH
    font_path = font.as_uri() if font.exists() else None
    play_button = resources.DEFAULT_VIDEO_BUTTON_PATH.as_uri()

    templates_dir = Path(__file__).parent.parent.parent / "src" / "nonebot_plugin_parser" / "renders" / "templates"

    artifact = await render_template(
        template_path=str(templates_dir),
        template_name="card.html.jinja",
        variables={
            "result": result,
            "logo": logo_path,
            "font": font_path,
            "play_button": play_button,
        },
        width=800,
    )

    out_path = tmp_path / "test_render_pixiv_html.png"
    png_bytes = bytes(artifact)
    out_path.write_bytes(png_bytes)
    logger.info(f"HTML 渲染图片已保存: {out_path} ({len(png_bytes) / 1024:.1f} KB)")
