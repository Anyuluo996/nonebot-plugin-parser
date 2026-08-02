import pytest
from nonebot import logger


class FakeStreamResponse:
    def __init__(self, *, status_code: int = 200, headers: dict[str, str], chunks: list[bytes]):
        self.status_code = status_code
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
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str],
        chunks: list[bytes],
        responses: list[tuple[int, dict[str, str], list[bytes]]] | None = None,
    ):
        self._responses = responses or [(status_code, headers, chunks)]
        self.calls = 0

    def stream(self, method: str, url: str, headers: dict[str, str] | None = None, follow_redirects: bool = True):
        index = min(self.calls, len(self._responses) - 1)
        status_code, response_headers, chunks = self._responses[index]
        self.calls += 1
        return FakeStreamResponse(
            status_code=status_code,
            headers=response_headers,
            chunks=chunks,
        )


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
        status_code=200,
        headers={"Transfer-Encoding": "chunked"},
        chunks=[b"hello", b"world"],
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
        status_code=200,
        headers={},
        chunks=[b"a" * (1024 * 1024), b"b"],
    )
    monkeypatch.setattr(pconfig, "parser_max_size", 1)

    with pytest.raises(IgnoreException):
        await downloader.download_file("https://example.com/large-video.mp4")

    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_download_file_follows_redirect_without_losing_success_path(tmp_path):
    from nonebot_plugin_parser.download import StreamDownloader

    downloader = StreamDownloader()
    await downloader.client.aclose()
    downloader.cache_dir = tmp_path
    downloader.client = FakeStreamClient(
        headers={},
        chunks=[],
        responses=[
            (302, {"Location": "https://cdn.example.com/video.mp4"}, []),
            (200, {"Transfer-Encoding": "chunked"}, [b"hello", b"world"]),
        ],
    )

    path = await downloader.download_file("https://example.com/video.mp4")

    assert downloader.client.calls == 2
    assert path.exists()
    assert path.read_bytes() == b"helloworld"


@pytest.mark.asyncio
async def test_download_file_retries_after_redirected_567(tmp_path):
    from nonebot_plugin_parser.download import StreamDownloader

    downloader = StreamDownloader()
    await downloader.client.aclose()
    downloader.cache_dir = tmp_path
    downloader.client = FakeStreamClient(
        headers={},
        chunks=[],
        responses=[
            (302, {"Location": "https://cdn.example.com/video.mp4"}, []),
            (567, {}, []),
            (302, {"Location": "https://cdn.example.com/video.mp4"}, []),
            (200, {"Transfer-Encoding": "chunked"}, [b"hello", b"world"]),
        ],
    )

    path = await downloader.download_file("https://example.com/video.mp4")

    assert downloader.client.calls == 4
    assert path.exists()
    assert path.read_bytes() == b"helloworld"


def test_bypass_proxy_default_direct(monkeypatch):
    """默认 parser_douyin_cdn_via_proxy=False：抖音 CDN 域名绕过代理（直连）"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.download import _bypass_proxy

    monkeypatch.setattr(pconfig, "parser_douyin_cdn_via_proxy", False)
    # 抖音 CDN 域名 → 直连（绕过代理）
    assert _bypass_proxy("https://p5-sign.douyinpic.com/cover.webp") is True
    assert _bypass_proxy("https://aweme.snssdk.com/aweme/v1/play/?video_id=x") is True
    # 非抖音域名 → 不绕过（走代理）
    assert _bypass_proxy("https://example.com/video.mp4") is False


def test_bypass_proxy_when_via_proxy(monkeypatch):
    """parser_douyin_cdn_via_proxy=True：抖音 CDN 域名改走代理"""
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.download import _bypass_proxy

    monkeypatch.setattr(pconfig, "parser_douyin_cdn_via_proxy", True)
    # 抖音 CDN 域名 → 不绕过（走代理）
    assert _bypass_proxy("https://p5-sign.douyinpic.com/cover.webp") is False
    assert _bypass_proxy("https://aweme.snssdk.com/aweme/v1/play/?video_id=x") is False


# ── P2P host 过滤 + backup 轮换 ──────────────────────────────────────
# 实测根因: B站把 os 参数伪装成 bcache, 只能靠 host 判别 mcdn P2P 节点;
# 这些节点速度仅 1-2M/s 且频繁 Connection reset, backup_url 里必然有正规 CDN。

_MCDN_HOST = "https://xy116x196x156x92xy.mcdn.bilivideo.cn:8082/v1/resource/xxx"
_MCDN_EDGE = "https://b-baaa9i31dolg49uwpj3u2qiz4d.edge.mountaintoys.cn:4483/upgcxcode/xxx"
_NORMAL_HOST = "https://cn-sccd-ct-02-19.bilivideo.com/upgcxcode/xxx"
_NORMAL_MIRROR = "https://upos-sz-mirrorcoso1.bilivideo.com/upgcxcode/xxx"


def test_is_p2p_node_detects_mcdn_variants():
    from nonebot_plugin_parser.parsers.bilibili import _is_p2p_node

    assert _is_p2p_node(_MCDN_HOST) is True
    assert _is_p2p_node(_MCDN_EDGE) is True
    assert _is_p2p_node(_NORMAL_HOST) is False
    assert _is_p2p_node(_NORMAL_MIRROR) is False


def test_select_preferred_streams_promotes_normal_cdn():
    """主链命中 P2P 时, 第一个正规 CDN 提到主链, 原 P2P 主链降级到 backup"""
    from nonebot_plugin_parser.parsers.bilibili import _select_preferred_streams

    primary, backups = _select_preferred_streams(_MCDN_HOST, [_NORMAL_HOST, _NORMAL_MIRROR])

    assert primary == _NORMAL_HOST  # 提升第一个正规 CDN
    assert _MCDN_HOST in backups  # 原 P2P 主链保留在 backup
    assert _NORMAL_MIRROR in backups
    assert primary not in backups  # 主链不在 backup 里(去重)


def test_select_preferred_streams_keeps_normal_primary():
    """主链本身是正规 CDN 时不变"""
    from nonebot_plugin_parser.parsers.bilibili import _select_preferred_streams

    primary, backups = _select_preferred_streams(_NORMAL_HOST, [_MCDN_HOST, _NORMAL_MIRROR])

    assert primary == _NORMAL_HOST
    assert _MCDN_HOST in backups
    assert _NORMAL_MIRROR in backups


def test_select_preferred_streams_all_p2p_keeps_primary():
    """主链和 backup 全是 P2P 时保持主链(有总比没有强)"""
    from nonebot_plugin_parser.parsers.bilibili import _select_preferred_streams

    primary, backups = _select_preferred_streams(_MCDN_HOST, [_MCDN_EDGE])

    assert primary == _MCDN_HOST
    assert backups == [_MCDN_EDGE]


def test_select_preferred_streams_empty_backup():
    """backup 为空时保持主链"""
    from nonebot_plugin_parser.parsers.bilibili import _select_preferred_streams

    primary, backups = _select_preferred_streams(_MCDN_HOST, [])

    assert primary == _MCDN_HOST
    assert backups == []


def test_select_preferred_streams_dedup():
    """B站偶发返回重复链接时去重"""
    from nonebot_plugin_parser.parsers.bilibili import _select_preferred_streams

    primary, backups = _select_preferred_streams(_MCDN_HOST, [_NORMAL_HOST, _NORMAL_HOST, _NORMAL_MIRROR])

    assert primary == _NORMAL_HOST
    # 重复的 _NORMAL_HOST 被去重, 只剩 _NORMAL_MIRROR + 原 mcdn 主链
    assert backups.count(_NORMAL_HOST) == 0  # 已提升为主链, backup 不应再有
    assert _MCDN_HOST in backups
    assert _NORMAL_MIRROR in backups
    assert len(backups) == 2  # mcdn主链 + mirror, 无重复


@pytest.mark.asyncio
async def test_download_file_rotates_backup_urls_on_retry(tmp_path):
    """重试时轮换 backup_urls, 而非重试同一个坏 URL"""
    import httpx

    from nonebot_plugin_parser.download import StreamDownloader

    requested_urls: list[str] = []

    class FailingThenOkClient:
        def __init__(self):
            self.calls = 0

        def stream(self, method, url, headers=None, follow_redirects=True):
            self.calls += 1
            requested_urls.append(url)
            if self.calls <= 2:
                # 前两次抛 HTTPError 触发重试
                raise httpx.ConnectError("simulated bad cdn")
            return FakeStreamResponse(status_code=200, headers={"Transfer-Encoding": "chunked"}, chunks=[b"ok"])

    downloader = StreamDownloader()
    await downloader.client.aclose()
    downloader.cache_dir = tmp_path
    downloader.client = FailingThenOkClient()

    path = await downloader.download_file(
        "https://primary.mcdn.bilivideo.cn/v.m4s",
        backup_urls=["https://backup0.bilivideo.com/v.m4s", "https://backup1.bilivideo.com/v.m4s"],
    )

    # 主链失败 → backup0 失败 → backup1 成功, 三次请求用了不同 URL
    assert len(requested_urls) == 3
    assert requested_urls[0] == "https://primary.mcdn.bilivideo.cn/v.m4s"
    assert requested_urls[1] == "https://backup0.bilivideo.com/v.m4s"
    assert requested_urls[2] == "https://backup1.bilivideo.com/v.m4s"
    assert path.read_bytes() == b"ok"
