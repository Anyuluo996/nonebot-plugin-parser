import pytest
from nonebot import logger


class FakeStreamResponse:
    def __init__(self, *, headers: dict[str, str], chunks: list[bytes]):
        self.headers = headers
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size: int):
        for chunk in self._chunks:
            yield chunk


class FakeStreamClient:
    def __init__(self, response: FakeStreamResponse):
        self.response = response

    def stream(self, method: str, url: str, headers: dict[str, str] | None = None, follow_redirects: bool = True):
        return self.response


def test_generate_file_name():
    import random

    from nonebot_plugin_parser.utils import generate_file_name

    suffix_lst = [
        ".jpg",
        ".png",
        ".gif",
        ".webp",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".ico",
        ".svg",
        ".heic",
        ".heif",
    ]
    # 测试 100 个链接
    for i in range(20):
        url = f"https://www.google.com/test{i}{random.choice(suffix_lst)}"
        file_name = generate_file_name(url)
        new_file_name = generate_file_name(url)
        assert file_name == new_file_name
        logger.info(f"{url}: {file_name}")


def test_limited_size_dict():
    from nonebot_plugin_parser.download.ytdlp import LimitedSizeDict

    limited_size_dict = LimitedSizeDict()
    for i in range(20):
        limited_size_dict[f"test{i}"] = f"test{i}"
    assert len(limited_size_dict) == 20
    for i in range(20):
        assert limited_size_dict[f"test{i}"] == f"test{i}"
    for i in range(20, 30):
        limited_size_dict[f"test{i}"] = f"test{i}"
    assert len(limited_size_dict) == 20


@pytest.mark.asyncio
async def test_download_file_without_content_length(tmp_path):
    from nonebot_plugin_parser.download import StreamDownloader

    downloader = StreamDownloader()
    await downloader.client.aclose()
    downloader.cache_dir = tmp_path
    downloader.client = FakeStreamClient(
        FakeStreamResponse(headers={"Transfer-Encoding": "chunked"}, chunks=[b"hello", b"world"])
    )

    path = await downloader.download_file("https://example.com/video.mp4")

    assert path.exists()
    assert path.read_bytes() == b"helloworld"


@pytest.mark.asyncio
async def test_download_file_without_content_length_respects_max_size(tmp_path, monkeypatch):
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.download import StreamDownloader
    from nonebot_plugin_parser.exception import IgnoreException

    downloader = StreamDownloader()
    await downloader.client.aclose()
    downloader.cache_dir = tmp_path
    downloader.client = FakeStreamClient(
        FakeStreamResponse(headers={}, chunks=[b"a" * (1024 * 1024), b"b"])
    )
    monkeypatch.setattr(pconfig, "parser_max_size", 1)

    with pytest.raises(IgnoreException):
        await downloader.download_file("https://example.com/large-video.mp4")

    assert not any(tmp_path.iterdir())
