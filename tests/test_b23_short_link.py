"""B站短链解析测试"""
import asyncio
import pytest
from nonebot import logger


@pytest.mark.asyncio
async def test_b23_short_link():
    """测试 b23.tv 短链解析"""
    from nonebot_plugin_parser.parsers import BilibiliParser

    test_urls = [
        "https://b23.tv/KQea23y",  # 测试短链
    ]

    parser = BilibiliParser()

    for url in test_urls:
        logger.info(f"开始测试 B站短链: {url}")

        # 测试 search_url 是否能匹配
        keyword, searched = parser.search_url(url)
        logger.info(f"匹配到的关键词: {keyword}")
        logger.info(f"匹配结果: {searched.group(0)}")

        # 测试解析
        result = await parser.parse(keyword, searched)

        logger.info(f"标题: {result.title}")
        logger.info(f"作者: {result.author}")
        logger.info(f"内容数量: {len(result.contents)}")

        assert result.title, "标题为空"
        assert result.author, "作者为空"
        assert len(result.contents) > 0, "内容为空"

        # 检查是否有视频内容
        video_contents = [c for c in result.contents if c.__class__.__name__ == "VideoContent"]
        logger.info(f"视频内容数量: {len(video_contents)}")

        for content in result.contents:
            logger.info(f"内容类型: {content.__class__.__name__}")
            if hasattr(content, "path_task"):
                try:
                    path = await content.get_path()
                    logger.info(f"文件路径: {path}")
                    if path:
                        logger.info(f"文件存在: {path.exists()}")
                except Exception as e:
                    logger.warning(f"获取文件路径失败: {type(e).__name__}: {e}")

        logger.success(f"B站短链解析成功: {result.title}")


@pytest.mark.asyncio
async def test_bv_url_with_params():
    """测试带多个参数的 BV URL"""
    from nonebot_plugin_parser.parsers import BilibiliParser

    # 模拟 b23.tv 重定向后的 URL
    test_urls = [
        "https://www.bilibili.com/video/BV19sNczXEQ4?buvid=XU427BB49086AB1A024C562114777E9705C34&p=1",
        "https://www.bilibili.com/video/BV19sNczXEQ4",
        "https://bilibili.com/video/BV19sNczXEQ4?p=1",
    ]

    parser = BilibiliParser()

    for url in test_urls:
        logger.info(f"测试 URL: {url}")

        try:
            keyword, searched = parser.search_url(url)
            logger.info(f"匹配到的关键词: {keyword}")
            logger.info(f"匹配结果: {searched.group(0)}")

            result = await parser.parse(keyword, searched)
            logger.success(f"解析成功: {result.title}")
        except Exception as e:
            import traceback
            logger.error(f"解析失败: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_b23_short_link())
