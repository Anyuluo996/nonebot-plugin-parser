import asyncio
from pathlib import Path
from functools import partial
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import aiofiles
from httpx import Proxy, HTTPError, AsyncClient
from nonebot import logger
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    DownloadColumn,
)

from .task import auto_task
from ..utils import merge_av, safe_unlink, generate_file_name
from ..config import pconfig
from ..constants import COMMON_HEADER, DOWNLOAD_TIMEOUT
from ..exception import IgnoreException, DownloadException

# Referer 白名单：域名 → Referer 值
_REFERRER_MAP: dict[str, str] = {
    "img.nga.178.com": "https://nga.178.com/",
}


# curl 专用域名（httpx 会被检测拦截或受代理环境变量影响导致 TLS 握手失败）
_CURL_ONLY_DOMAINS: frozenset[str] = frozenset({
    "img.nga.178.com",
    # 抖音 CDN 域名（国内 CDN，走代理会导致 TLS 握手失败）
    "qtaeixd.com",
    "qtlde.com",
    "douyinvod.com",
    "douyinvod.net",
    "bytevcloud.com",
    "bytevc.com",
    "douyinpic.com",
    "pstatp.com",
    "byteimg.com",
    "amemv.com",
    "bytecdn.cn",
    "douyinstatic.com",
})

# 不走代理的域名（抖音 CDN 在国内，走代理反而容易 TLS 握手失败）
_NO_PROXY_DOMAINS: frozenset[str] = frozenset({
    "douyinvod.com",
    "douyinvod.net",
    "bytevcloud.com",
    "bytevc.com",
    "douyinpic.com",
    "pstatp.com",
    "byteimg.com",
    "amemv.com",
    "bytecdn.cn",
    "douyinstatic.com",
})
_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


class _RetryDownload(Exception):
    """Signal that the current request should be retried."""


class _FollowRedirect(Exception):
    """Signal that the current request should continue with a redirect target."""

    def __init__(self, location: str):
        self.location = location


def _auto_referer(url: str) -> str | None:
    """根据 URL 域名返回应使用的 Referer，不在白名单中返回 None"""
    try:
        netloc = urlparse(url).netloc.rsplit(":", 1)[0]
        return _REFERRER_MAP.get(netloc)
    except Exception:
        return None


def _use_curl(url: str) -> bool:
    """判断该 URL 是否走 curl 下载"""
    try:
        netloc = urlparse(url).netloc.rsplit(":", 1)[0]
        return any(netloc == d or netloc.endswith("." + d) for d in _CURL_ONLY_DOMAINS)
    except Exception:
        return False


def _bypass_proxy(url: str) -> bool:
    """判断该 URL 是否应绕过代理（抖音等国内 CDN）"""
    try:
        netloc = urlparse(url).netloc.rsplit(":", 1)[0]
        return any(netloc == d or netloc.endswith("." + d) for d in _NO_PROXY_DOMAINS)
    except Exception:
        return False


async def _download_by_curl(
    url: str,
    file_path: Path,
    headers: dict[str, str],
    max_retries: int = 3,
) -> Path:
    """使用 curl_cffi 下载文件（模拟浏览器绕过检测），支持重试"""
    from curl_cffi.requests import AsyncSession

    max_size_bytes = pconfig.max_size * 1024 * 1024
    impersonate = "chrome110"
    # CDN 域名不走代理（国内 CDN 走代理会导致 TLS 握手失败）
    if _bypass_proxy(url):
        proxies = {"https": "", "http": ""}
    elif pconfig.proxy:
        proxies = {"https": pconfig.proxy, "http": pconfig.proxy}
    else:
        proxies = {"https": "", "http": ""}

    for attempt in range(max_retries + 1):
        try:
            async with AsyncSession(impersonate=impersonate) as session:
                resp = await session.get(
                    url, headers=headers, allow_redirects=True, proxies=proxies
                )
            status = resp.status_code

            if status == 567:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "媒体服务器返回 567 (疑似频率限制), "
                        "{}s 后重试 ({}/{}) | url: {}",
                        wait, attempt + 1, max_retries, url,
                    )
                    await asyncio.sleep(wait)
                    continue
                await safe_unlink(file_path)
                logger.error("567 重试耗尽 | url: {}", url)
                raise DownloadException("媒体下载失败")

            if status != 200:
                await safe_unlink(file_path)
                logger.error("curl_cffi 下载失败 HTTP {} | url: {}", status, url)
                raise DownloadException("媒体下载失败")

            content_len = len(resp.content)
            if content_len == 0:
                await safe_unlink(file_path)
                logger.warning("媒体 url: {}, 大小为 0, 取消下载", url)
                raise IgnoreException

            if content_len > max_size_bytes:
                await safe_unlink(file_path)
                logger.warning(
                    "媒体 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                    url, content_len / 1024 / 1024, pconfig.max_size,
                )
                raise IgnoreException

            async with aiofiles.open(file_path, "wb") as f:
                await f.write(resp.content)

            return file_path

        except Exception as e:
            if attempt == max_retries:
                await safe_unlink(file_path)
                logger.exception("curl_cffi 下载异常 | url: {}", url)
                raise DownloadException("媒体下载失败")
            wait = 2 ** attempt
            logger.warning(
                "curl_cffi 下载异常: {}, {}s 后重试 ({}/{}) | url: {}",
                e, wait, attempt + 1, max_retries, url,
            )
            await asyncio.sleep(wait)
            continue

    return file_path


class StreamDownloader:
    """Downloader class for downloading files with stream"""

    def __init__(self):
        self.headers: dict[str, str] = COMMON_HEADER.copy()
        self.cache_dir: Path = pconfig.cache_dir
        proxy_url = pconfig.proxy
        proxy = Proxy(url=proxy_url) if proxy_url else None
        self.client: AsyncClient = AsyncClient(
            timeout=DOWNLOAD_TIMEOUT, verify=False, proxy=proxy
        )
        self.direct_client: AsyncClient = AsyncClient(
            timeout=DOWNLOAD_TIMEOUT, verify=False, trust_env=False,
        )

    async def close(self):
        """关闭下载器"""
        await self.client.aclose()
        await self.direct_client.aclose()

    @contextmanager
    def rich_progress(self, desc: str, total: int | None = None):
        with Progress(
            TextColumn("[bold blue]{task.description}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(),
        ) as progress:
            task_id = progress.add_task(description=desc, total=total)
            yield partial(progress.update, task_id)

    @auto_task
    async def download_file(
        self,
        url: str,
        *,
        file_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
        chunk_size: int = 64 * 1024,
        max_retries: int = 3,
    ) -> Path:
        """download file by url with stream"""
        if not file_name:
            file_name = generate_file_name(url)
        file_path = self.cache_dir / file_name
        if file_path.exists():
            return file_path

        headers = {**self.headers, **(ext_headers or {})}
        if "Referer" not in headers:
            if auto_ref := _auto_referer(url):
                headers["Referer"] = auto_ref

        use_curl_result = _use_curl(url)
        logger.debug("_use_curl check | url: {}, result: {}", url[:80], use_curl_result)
        if use_curl_result:
            return await _download_by_curl(url, file_path, headers, max_retries)

        client = self.direct_client if _bypass_proxy(url) else self.client
        max_size_bytes = pconfig.max_size * 1024 * 1024
        max_redirects = 10

        for attempt in range(max_retries + 1):
            redirect_count = 0
            current_url = url
            try:
                while True:
                    try:
                        async with client.stream(
                            "GET", current_url, headers=headers, follow_redirects=False,
                        ) as response:
                            status = response.status_code

                            if status == 567:
                                if attempt == max_retries:
                                    await safe_unlink(file_path)
                                    logger.error("567 重试耗尽 | url: {}", current_url)
                                    raise DownloadException("媒体下载失败")
                                raise _RetryDownload

                            if status in _REDIRECT_STATUSES:
                                redirect_url = response.headers.get("Location")
                                if redirect_url:
                                    redirect_count += 1
                                    if redirect_count > max_redirects:
                                        await safe_unlink(file_path)
                                        logger.error(
                                            "重定向次数超过上限 {} | url: {}",
                                            max_redirects, current_url,
                                        )
                                        raise DownloadException("媒体下载失败")
                                    raise _FollowRedirect(urljoin(current_url, redirect_url))

                            if status != 200:
                                response.raise_for_status()

                            content_length_header = response.headers.get("Content-Length")
                            try:
                                content_length = (
                                    int(content_length_header) if content_length_header else None
                                )
                            except ValueError:
                                content_length = None

                            if content_length == 0:
                                logger.warning(
                                    "媒体 url: {}, 大小为 0, 取消下载", current_url,
                                )
                                raise IgnoreException

                            if content_length is not None and (
                                file_size := content_length / 1024 / 1024
                            ) > pconfig.max_size:
                                logger.warning(
                                    "媒体 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                                    current_url, file_size, pconfig.max_size,
                                )
                                raise IgnoreException

                            downloaded_size = 0
                            exceed_size_limit = False
                            with self.rich_progress(
                                file_name, content_length,
                            ) as update_progress:
                                async with aiofiles.open(file_path, "wb") as file:
                                    async for chunk in response.aiter_bytes(chunk_size):
                                        if not chunk:
                                            continue

                                        downloaded_size += len(chunk)
                                        if content_length is None and downloaded_size > max_size_bytes:
                                            exceed_size_limit = True
                                            break

                                        await file.write(chunk)
                                        update_progress(advance=len(chunk))

                            if exceed_size_limit:
                                await safe_unlink(file_path)
                                file_size = downloaded_size / 1024 / 1024
                                logger.warning(
                                    "媒体 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                                    current_url, file_size, pconfig.max_size,
                                )
                                raise IgnoreException

                            if downloaded_size == 0:
                                await safe_unlink(file_path)
                                logger.warning(
                                    "媒体 url: {}, 大小为 0, 取消下载", current_url,
                                )
                                raise IgnoreException

                            return file_path
                    except _FollowRedirect as redirect:
                        current_url = redirect.location
                        continue
            except _RetryDownload:
                wait = 2 ** attempt
                logger.warning(
                    "媒体服务器返回 567 (疑似频率限制), "
                    "{}s 后重试 ({}/{}) | url: {}",
                    wait, attempt + 1, max_retries, current_url,
                )
                await asyncio.sleep(wait)
                continue

            except HTTPError:
                if attempt == max_retries:
                    await safe_unlink(file_path)
                    logger.exception(
                        "下载失败 | url: {}, file_path: {}", current_url, file_path,
                    )
                    raise DownloadException("媒体下载失败")
                wait = 2 ** attempt
                logger.warning(
                    "下载异常, {}s 后重试 ({}/{}) | url: {}",
                    wait, attempt + 1, max_retries, current_url,
                )
                await asyncio.sleep(wait)

        return file_path

    @auto_task
    async def download_video(
        self,
        url: str,
        *,
        video_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download video file by url with stream"""
        if video_name is None:
            video_name = generate_file_name(url, ".mp4")
        return await self.download_file(url, file_name=video_name, ext_headers=ext_headers, chunk_size=1024 * 1024)

    @auto_task
    async def download_audio(
        self,
        url: str,
        *,
        audio_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download audio file by url with stream"""
        if audio_name is None:
            audio_name = generate_file_name(url, ".mp3")
        return await self.download_file(url, file_name=audio_name, ext_headers=ext_headers)

    @auto_task
    async def download_img(
        self,
        url: str,
        *,
        img_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download image file by url with stream"""
        if img_name is None:
            img_name = generate_file_name(url, ".jpg")
        return await self.download_file(url, file_name=img_name, ext_headers=ext_headers)

    @auto_task
    async def download_av_and_merge(
        self,
        v_url: str,
        a_url: str,
        *,
        output_path: Path,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download video and audio file by url with stream and merge"""
        v_path, a_path = await asyncio.gather(
            self.download_video(v_url, ext_headers=ext_headers),
            self.download_audio(a_url, ext_headers=ext_headers),
        )
        await merge_av(v_path=v_path, a_path=a_path, output_path=output_path)
        return output_path

    async def download_imgs_without_raise(
        self,
        urls: list[str],
        *,
        ext_headers: dict[str, str] | None = None,
    ) -> list[Path]:
        """download image files by urls with stream, ignore errors"""
        paths_or_errs = await asyncio.gather(
            *[self.download_img(url, ext_headers=ext_headers) for url in urls],
            return_exceptions=True,
        )
        return [p for p in paths_or_errs if isinstance(p, Path)]

    @auto_task
    async def download_m3u8(
        self,
        m3u8_url: str,
        *,
        video_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download m3u8 file by url with stream"""
        if video_name is None:
            video_name = generate_file_name(m3u8_url, ".mp4")

        video_path = pconfig.cache_dir / video_name

        try:
            async with aiofiles.open(video_path, "wb") as f:
                total_size = 0
                with self.rich_progress(desc=video_name) as update_progress:
                    for url in await self._get_m3u8_slices(m3u8_url):
                        slice_client = self.direct_client if _bypass_proxy(url) else self.client
                        async with slice_client.stream("GET", url, headers=ext_headers) as response:
                            async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                                await f.write(chunk)
                                total_size += len(chunk)
                                update_progress(advance=len(chunk), total=total_size)
        except HTTPError:
            await safe_unlink(video_path)
            logger.exception("m3u8 视频下载失败")
            raise DownloadException("m3u8 视频下载失败")

        return video_path

    async def _get_m3u8_slices(self, m3u8_url: str):
        """获取 m3u8 分片"""

        m3u8_client = self.direct_client if _bypass_proxy(m3u8_url) else self.client
        response = await m3u8_client.get(m3u8_url)
        response.raise_for_status()

        slices_text = response.text
        slices: list[str] = []

        for line in slices_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slices.append(urljoin(m3u8_url, line))

        return slices


DOWNLOADER: StreamDownloader = StreamDownloader()

try:
    import yt_dlp as yt_dlp

    from .ytdlp import YtdlpDownloader

    YTDLP_DOWNLOADER = YtdlpDownloader()
except ImportError:
    YTDLP_DOWNLOADER = None
