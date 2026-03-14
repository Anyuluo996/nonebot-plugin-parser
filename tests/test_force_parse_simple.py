import asyncio

import pytest


def _platform():
    from nonebot_plugin_parser.parsers import Platform

    return Platform(name="test", display_name="Test")


@pytest.mark.asyncio
async def test_dynamic_content_get_gif_path_materializes_task(tmp_path):
    from nonebot_plugin_parser.parsers import DynamicContent

    video_path = tmp_path / "video.mp4"
    gif_path = tmp_path / "video.gif"
    cover_path = tmp_path / "cover.jpg"
    video_path.write_bytes(b"video")
    gif_path.write_bytes(b"gif")
    cover_path.write_bytes(b"cover")

    async def create_gif():
        return gif_path

    async def create_cover():
        return cover_path

    content = DynamicContent(
        video_path,
        gif_path=asyncio.create_task(create_gif()),
        cover=asyncio.create_task(create_cover()),
    )

    assert await content.get_gif_path() == gif_path
    assert content.gif_path == gif_path
    assert await content.get_cover_path() == cover_path
    assert content.cover == cover_path


@pytest.mark.asyncio
async def test_parse_result_tracks_dynamic_content_pending_resources(tmp_path):
    from nonebot_plugin_parser.parsers import ParseResult, DynamicContent

    video_path = tmp_path / "video.mp4"
    gif_path = tmp_path / "video.gif"
    cover_path = tmp_path / "cover.jpg"
    video_path.write_bytes(b"video")
    gif_path.write_bytes(b"gif")
    cover_path.write_bytes(b"cover")

    async def create_path(path):
        await asyncio.sleep(0)
        return path

    content = DynamicContent(
        asyncio.create_task(create_path(video_path)),
        gif_path=asyncio.create_task(create_path(gif_path)),
        cover=asyncio.create_task(create_path(cover_path)),
    )
    result = ParseResult(platform=_platform(), contents=[content])

    assert result._has_pending_resources() is True
    await result.ensure_downloads_complete()
    assert result._has_pending_resources() is False
    assert await result.cover_path() == cover_path
    assert result.content_type == "动态"


def test_twitter_collect_result_routes_gif_to_dynamic_content(monkeypatch, tmp_path):
    from nonebot_plugin_parser.parsers import Author, VideoContent, DynamicContent
    from nonebot_plugin_parser.parsers.twitter import (
        MediaElement,
        TwitterParser,
        VxTwitterResponse,
    )

    parser = TwitterParser()
    video_path = tmp_path / "video.mp4"
    gif_path = tmp_path / "gif.mp4"
    video_path.write_bytes(b"video")
    gif_path.write_bytes(b"gif")

    captured: list[tuple[list[str], bool, str | None]] = []

    def fake_create_dynamic_contents(
        dynamic_urls: list[str],
        convert_to_gif: bool = False,
        cover_url: str | None = None,
    ):
        captured.append((dynamic_urls, convert_to_gif, cover_url))
        return [DynamicContent(gif_path)]

    monkeypatch.setattr(parser, "create_dynamic_contents", fake_create_dynamic_contents)
    monkeypatch.setattr(
        parser,
        "create_video_content",
        lambda url, cover_url=None: VideoContent(video_path),
    )
    monkeypatch.setattr(parser, "create_author", lambda *args, **kwargs: Author(name="tester"))

    data = VxTwitterResponse(
        article=None,
        date_epoch=1,
        fetched_on=1,
        likes=1,
        text="tweet",
        user_name="tester",
        user_screen_name="tester",
        user_profile_image_url="https://example.com/avatar.jpg",
        media_extended=[
            MediaElement(type="gif", url="https://example.com/gif.mp4", thumbnail_url="https://example.com/gif.jpg"),
            MediaElement(type="video", url="https://example.com/video.mp4", thumbnail_url="https://example.com/video.jpg"),
        ],
    )

    result = parser._collect_result(data)

    assert len(result.dynamic_contents) == 1
    assert len(result.video_contents) == 1
    assert captured == [(["https://example.com/gif.mp4"], True, "https://example.com/gif.jpg")]


@pytest.mark.asyncio
async def test_renderer_uses_dynamic_gif_and_emits_dynamic_only_message(monkeypatch, tmp_path):
    import nonebot_plugin_parser.renders.base as render_base
    from nonebot_plugin_parser.parsers import ParseResult, DynamicContent
    from nonebot_plugin_parser.renders.base import BaseRenderer

    class DummyRenderer(BaseRenderer):
        async def render_messages(self, result):
            if False:
                yield result

    video_path = tmp_path / "video.mp4"
    gif_path = tmp_path / "video.gif"
    video_path.write_bytes(b"video")
    gif_path.write_bytes(b"gif")

    monkeypatch.setattr(render_base, "UniMessage", lambda value: value)
    monkeypatch.setattr(render_base.UniHelper, "img_seg", lambda path: f"img:{path.name}")
    monkeypatch.setattr(render_base.UniHelper, "video_seg", lambda path: f"video:{path.name}")
    monkeypatch.setattr(render_base.UniHelper, "construct_forward_message", lambda segs: ["forward", *segs])
    monkeypatch.setattr(render_base.pconfig, "parser_need_forward_contents", False)

    result = ParseResult(platform=_platform(), contents=[DynamicContent(video_path, gif_path=gif_path)])
    renderer = DummyRenderer()

    messages = [message async for message in renderer.render_contents(result)]

    assert messages == [["img:video.gif"]]


def test_parse_result_cache_validity_checks_local_files(tmp_path):
    from nonebot_plugin_parser.parsers import ParseResult, ImageContent

    image_path = tmp_path / "image.png"
    render_path = tmp_path / "render.png"
    image_path.write_bytes(b"image")
    render_path.write_bytes(b"render")

    valid = ParseResult(
        platform=_platform(),
        contents=[ImageContent(image_path)],
        render_image=render_path,
    )
    invalid = ParseResult(platform=_platform(), contents=[ImageContent(tmp_path / "missing.png")])

    assert valid.is_cache_valid() is True
    assert invalid.is_cache_valid() is False


def test_result_cache_uses_deepcopy_and_skips_invalid_results(tmp_path):
    from nonebot_plugin_parser.parsers import ParseResult, ImageContent
    from nonebot_plugin_parser.matchers import (
        _RESULT_CACHE,
        _cache_result,
        _get_cached_result,
        clear_result_cache,
    )

    clear_result_cache()
    image_path = tmp_path / "cache.png"
    render_path = tmp_path / "render.png"
    image_path.write_bytes(b"cache")
    render_path.write_bytes(b"render")
    result = ParseResult(
        platform=_platform(),
        title="origin",
        contents=[ImageContent(image_path)],
        render_image=render_path,
    )

    _cache_result("ok", result)
    cached = _get_cached_result("ok")
    assert cached is not None
    assert cached is not result
    assert cached.contents is not result.contents

    cached.title = "changed"
    cached.contents.append(ImageContent(image_path))
    cached_again = _get_cached_result("ok")
    assert cached_again is not None
    assert cached_again.title == "origin"
    assert len(cached_again.contents) == 1

    invalid = ParseResult(platform=_platform(), contents=[ImageContent(tmp_path / "missing.png")])
    _cache_result("bad", invalid)
    assert _get_cached_result("bad") is None
    assert "bad" not in _RESULT_CACHE

    clear_result_cache()


@pytest.mark.asyncio
async def test_stream_downloader_close_closes_client():
    from nonebot_plugin_parser.download import StreamDownloader

    downloader = StreamDownloader()
    await downloader.close()
    assert downloader.client.is_closed
