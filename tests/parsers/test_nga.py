"""测试NGA解析器"""

import pytest
from nonebot import logger


@pytest.mark.asyncio
async def test_nga_parse():
    """测试NGA帖子解析：主楼 + 前 4 楼回复，渲染成图片"""
    from nonebot_plugin_parser.parsers.nga import NGAParser

    url = "https://bbs.nga.cn/read.php?tid=47058130"
    parser = NGAParser()

    # 测试URL匹配
    keyword, searched = parser.search_url(url)

    assert searched, "URL应该能被NGA解析器匹配"

    # 测试解析
    result = await parser.parse(keyword, searched)

    # ── 主楼断言 ──
    assert result.title, "应该能提取标题"
    assert result.platform.name == "nga"
    assert result.author is not None, "应该能提取主楼作者信息"
    assert result.timestamp is not None, "应该能提取主楼发布时间"
    assert result.graphics, "应该能提取主楼图文内容"

    logger.debug(f"标题: {result.title}")
    logger.debug(f"主楼作者: {result.author.name if result.author else 'N/A'}")
    logger.debug(f"主楼时间: {result.formartted_datetime}")
    logger.debug(f"主楼graphics: {result.graphics}")

    # ── 回复楼层断言 ──
    posts = result.extra.get("posts")
    assert isinstance(posts, list), "extra.posts 应为回复楼层列表"
    assert len(posts) > 0, "应至少提取到 1 楼回复"
    assert len(posts) <= 4, "回复楼层数不应超过 4"

    for post in posts:
        assert "floor" in post, "每楼应有 floor 字段"
        assert "uid" in post, "每楼应有 uid 字段（尝试获取 id）"
        assert "name" in post, "每楼应有 name 字段"
        assert "text" in post, "每楼应有 text 字段"
        assert "images" in post, "每楼应有 images 字段"
        logger.debug(f"  {post['floor']}F uid={post['uid']} name={post['name']}")

    # ── 下载资源（主楼 graphics + 回复楼 images） ──
    await result.ensure_downloads_complete()
    logger.success("NGA帖子解析成功")

    # ── 验证回复楼 images 下载后可取到本地路径 ──
    for post in posts:
        for img in post["images"]:
            if hasattr(img, "path_uri"):
                assert img.path_uri is not None, "回复楼图片下载后应有 path_uri"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
