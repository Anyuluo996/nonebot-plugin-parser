"""2026-10-26 全面审查修复的回归用例（离线，不依赖网络）。

覆盖：
- B 站 URL 形式 `?p=N` 分P 参数
- ILLU 文章/图集 keyword 不再互相覆盖
- 失败的内嵌图不再让缓存 deepcopy 抛 TypeError
- 封面/GIF 超时后不残留已取消 Task（避免 CancelledError 穿透 except Exception）
- tdl 进程清理按 argv[0] 精确匹配（不再子串误杀）
- 下载临时文件 → 原子发布
- 贴吧主楼/楼层视频走 contents 通道
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_bilibili_url_page_param():
    """URL 形式的 ?p=N 必须被 pattern 捕获（曾恒为第 1 P）。"""
    from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

    keyword, matched = BilibiliParser.search_url("https://www.bilibili.com/video/BV1xx411c7mD?p=3")
    assert keyword == "/BV"
    assert matched.group("page_num") == "3"

    _, matched_with_extra = BilibiliParser.search_url(
        "https://www.bilibili.com/video/BV1xx411c7mD/?p=12&spm_id_from=333.999"
    )
    assert matched_with_extra.group("page_num") == "12"

    _, matched_without = BilibiliParser.search_url("https://www.bilibili.com/video/BV1xx411c7mD")
    assert matched_without.group("page_num") is None

    _, av_matched = BilibiliParser.search_url("https://www.bilibili.com/video/av170001?p=2")
    assert av_matched.group("page_num") == "2"


def test_illu_handlers_are_keys_distinct():
    """文章/图集 handler 不能共用 keyword（后者会覆盖前者并抛 IndexError）。"""
    from nonebot_plugin_parser.parsers.illu import IlluParser

    handlers = IlluParser._handlers
    assert handlers["articleId%3D"].__name__ == "parse_article"
    assert handlers["mainid%3D"].__name__ == "parse_drawing"

    article_url = (
        "https://illund.com/share.html?al=mindlib%3A%2F%2Freactbox%2F"
        "%3FarticleId%3Dabc123%26hideTitleBar%3D1"
    )
    drawing_url = "https://illund.com/share.html?al=mindlib%3A%2F%2Freactbox%2F%3Fmainid%3Dfcecc2da36"

    keyword, matched = IlluParser.search_url(article_url)
    assert handlers[keyword].__name__ == "parse_article"
    assert matched.group("articleId") == "abc123"

    keyword, _ = IlluParser.search_url(drawing_url)
    assert handlers[keyword].__name__ == "parse_drawing"


@pytest.mark.asyncio
async def test_failed_extra_image_invalidates_cache(tmp_path):
    """extra 内嵌图下载失败时 is_cache_valid 必须为 False（否则 deepcopy 抛 TypeError）。"""
    from copy import deepcopy

    from nonebot_plugin_parser.parsers.data import ImageContent, ParseResult, Platform

    async def boom():
        raise RuntimeError("download failed")

    failed_task = asyncio.create_task(boom())
    with pytest.raises(RuntimeError):
        await failed_task

    render_image = tmp_path / "card.png"
    render_image.write_bytes(b"png")

    result = ParseResult(
        platform=Platform("x", "X"),
        extra={"posts": [{"images": [ImageContent(failed_task)], "avatar": None}]},
        render_image=render_image,
    )

    assert result._has_pending_resources() is True
    assert result.is_cache_valid() is False

    # 兜底：即便有 Task 漏进缓存，deepcopy 失败也只应降级为不缓存（matters: 不能打死解析）
    from nonebot_plugin_parser.matchers import _cache_result, clear_result_cache

    clear_result_cache()
    result.extra = {}
    result.contents = []
    _cache_result("https://example.com/x", result)
    assert deepcopy(result).render_image == render_image


@pytest.mark.asyncio
async def test_cover_timeout_leaves_no_cancelled_task(monkeypatch):
    """wait_for 超时后字段必须置 None：否则二次取值抛 CancelledError（BaseException）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.exception import DownloadException
    from nonebot_plugin_parser.parsers.data import VideoContent

    monkeypatch.setattr(pconfig, "parser_video_send_timeout", 0.01)

    async def slow():
        await asyncio.sleep(5)

    cover_task = asyncio.create_task(slow())
    content = VideoContent(Path("dummy.mp4"), cover=cover_task)

    with pytest.raises(DownloadException):
        await content.get_cover_path()

    assert content.cover is None
    # 关键回归：第二次取值不再抛 CancelledError
    assert await content.get_cover_path() is None


@pytest.mark.asyncio
async def test_gif_failure_is_terminal(monkeypatch):
    """GIF 转换失败后重复取值仍抛 DownloadException（不能回退成 get_path 的 zip）。"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.exception import DownloadException
    from nonebot_plugin_parser.parsers.data import DynamicContent

    monkeypatch.setattr(pconfig, "parser_video_send_timeout", 1)

    async def bad_convert():
        raise ValueError("ffmpeg failed")

    content = DynamicContent(Path("ugoira.zip"), gif_path=asyncio.create_task(bad_convert()))

    with pytest.raises(DownloadException):
        await content.get_gif_path()
    assert content._gif_failed is True
    with pytest.raises(DownloadException):
        await content.get_gif_path()


def test_tdl_process_matching_is_exact():
    """tdl 清理只看 argv[0] 文件名：命令行里出现 tdl 的无关进程不能被误杀。"""
    from nonebot_plugin_parser.download.tdl import _is_our_tdl

    assert _is_our_tdl(["/usr/local/bin/tdl", "login", "--type", "qr"]) is True
    assert _is_our_tdl(["/opt/tdl/tdl.exe", "dl"]) is True
    # 旧实现的子串匹配会把这些全部 SIGKILL
    assert _is_our_tdl(["vim", "/root/tdl.py"]) is False
    assert _is_our_tdl(["tail", "-f", "/root/.tdl/log"]) is False
    assert _is_our_tdl(["python", "-m", "tdl_helper"]) is False
    assert _is_our_tdl([]) is False


def test_temp_path_publish_is_atomic(tmp_path):
    """下载落盘走临时文件 + 原子发布，成功后不残留 .part。"""
    from nonebot_plugin_parser.download import _publish, _temp_path

    final = tmp_path / "video.mp4"
    tmp = _temp_path(final)
    assert tmp != final
    assert tmp.parent == final.parent
    # 末位后缀必须仍是 .mp4: ffmpeg 按最后一个后缀猜容器格式
    assert tmp.suffix == final.suffix
    assert ".part" in tmp.name

    tmp.write_bytes(b"payload")
    published = _publish(tmp, final)

    assert published == final
    assert final.read_bytes() == b"payload"
    assert not tmp.exists()
    assert not list(tmp_path.glob("*.part"))


def test_tieba_video_contents_collected():
    """主楼 video_info 与楼层 FragVideo 都要进 contents，而不是画进长图。"""
    from nonebot_plugin_parser.parsers.tieba.models import Contents, FragVideo
    from nonebot_plugin_parser.parsers.tieba.utils import build_reply_floors, build_video_contents

    main_video = FragVideo(src="https://v/main.mp4", cover_src="https://c/main.jpg", duration=12, width=720)
    floor_video = FragVideo(src="https://v/floor.mp4", cover_src="https://c/floor.jpg", duration=8, width=720)

    thread = SimpleNamespace(contents=Contents(video=main_video))
    reply_post = SimpleNamespace(
        contents=Contents(objs=[floor_video]),
        floor=2,
        user=SimpleNamespace(portrait="", show_name="回帖人"),
        agree=1,
    )
    posts = SimpleNamespace(thread=thread, objs=[SimpleNamespace(contents=Contents(objs=[])), reply_post])

    videos = build_video_contents(posts, lambda src, cover_url, duration: (src, cover_url, duration))
    assert videos == [
        ("https://v/main.mp4", "https://c/main.jpg", 12),
        ("https://v/floor.mp4", "https://c/floor.jpg", 8),
    ]

    floors = build_reply_floors(posts, lambda url: ("img", url))
    assert floors[0]["images"] == []
