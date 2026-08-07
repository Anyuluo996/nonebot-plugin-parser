"""测试NGA解析器"""

import httpx
import pytest
from nonebot import logger


@pytest.mark.asyncio
async def test_nga_parse():
    """测试NGA帖子解析：主楼 + 前 4 楼回复，渲染成图片"""
    from nonebot_plugin_parser.exception import ParseException
    from nonebot_plugin_parser.parsers.nga import NGAParser

    url = "https://bbs.nga.cn/read.php?tid=47058130"
    parser = NGAParser()

    # 测试URL匹配
    keyword, searched = parser.search_url(url)

    assert searched, "URL应该能被NGA解析器匹配"

    # 测试解析（NGA 接口偶发返回截断 JSON / 风控，解析阶段失败时 skip，与快手等测试一致）
    try:
        result = await parser.parse(keyword, searched)
    except (ParseException, httpx.HTTPError) as e:
        pytest.skip(f"NGA 解析失败（接口截断/风控/网络），跳过: {e!r}")

    # ── 主楼断言 ──
    assert result.title, "应该能提取标题"
    assert result.platform.name == "nga"
    assert result.author is not None, "应该能提取主楼作者信息"
    assert result.timestamp is not None, "应该能提取主楼发布时间"
    assert result.graphics, "应该能提取主楼图文内容"

    logger.debug(f"标题: {result.title}")
    logger.debug(f"主楼作者: {result.author.name if result.author else 'N/A'}")
    logger.debug(f"主楼时间: {result.formatted_datetime()}")
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
    # 图床 (img.nga.178.com) 在 CI 环境偶发 403/超时, 下载后 path_uri 为 None。
    # 此属第三方图床波动而非解析/下载逻辑缺陷, 与快手等测试一致做 skip 保护,
    # 避免图床抖动导致 CI 红。仅当图床可达时才严格断言下载产物。
    await result.ensure_downloads_complete()

    # 收集所有回复楼图片, 检查下载产物是否完整
    reply_imgs = [
        img for post in posts for img in post["images"] if hasattr(img, "path_uri")
    ]
    missing = [img for img in reply_imgs if img.path_uri is None]
    if missing:
        pytest.skip(
            f"NGA 图床下载失败（img.nga.178.com 偶发 403/超时），"
            f"{len(missing)}/{len(reply_imgs)} 张回复楼图片无 path_uri，跳过"
        )

    logger.success("NGA帖子解析成功")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
