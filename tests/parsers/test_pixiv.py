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
