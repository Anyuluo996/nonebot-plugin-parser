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
    video_path.write_bytes(b"video")
    gif_path.write_bytes(b"gif")

    async def create_gif():
        return gif_path

    content = DynamicContent(video_path, gif_path=asyncio.create_task(create_gif()))

    assert await content.get_gif_path() == gif_path
    assert content.gif_path == gif_path


def test_parse_result_cache_validity_checks_local_files(tmp_path):
    from nonebot_plugin_parser.parsers import ImageContent, ParseResult

    image_path = tmp_path / "image.png"
    render_path = tmp_path / "render.png"
    image_path.write_bytes(b"image")
    render_path.write_bytes(b"render")

    valid = ParseResult(platform=_platform(), contents=[ImageContent(image_path)], render_image=render_path)
    invalid = ParseResult(platform=_platform(), contents=[ImageContent(tmp_path / "missing.png")])

    assert valid.is_cache_valid() is True
    assert invalid.is_cache_valid() is False


def test_result_cache_uses_deepcopy_and_skips_invalid_results(tmp_path):
    from nonebot_plugin_parser.matchers import _RESULT_CACHE, _cache_result, _get_cached_result, clear_result_cache
    from nonebot_plugin_parser.parsers import ImageContent, ParseResult

    clear_result_cache()
    image_path = tmp_path / "cache.png"
    image_path.write_bytes(b"cache")
    result = ParseResult(platform=_platform(), title="origin", contents=[ImageContent(image_path)])

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
async def test_stream_downloader_serializes_same_url_and_releases_lock(monkeypatch, tmp_path):
    from nonebot_plugin_parser.download import StreamDownloader

    StreamDownloader._download_locks.clear()
    StreamDownloader._download_lock_refs.clear()
    downloader = StreamDownloader()
    active_count = 0
    max_active_count = 0

    async def fake_download(url: str, file_name: str | None = None, ext_headers=None):
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
        path = tmp_path / (file_name or "file.bin")
        path.write_bytes(b"ok")
        active_count -= 1
        return path

    monkeypatch.setattr(downloader, "_download_file_internal", fake_download)
    url = "https://example.com/file.mp4"

    try:
        results = await asyncio.gather(
            downloader.streamd(url, file_name="same.mp4"),
            downloader.streamd(url, file_name="same.mp4"),
        )
        assert all(path.exists() for path in results)
        assert max_active_count == 1
        assert url not in StreamDownloader._download_locks
        assert url not in StreamDownloader._download_lock_refs
    finally:
        await downloader.close()
        StreamDownloader._download_locks.clear()
        StreamDownloader._download_lock_refs.clear()

    assert downloader.client.is_closed