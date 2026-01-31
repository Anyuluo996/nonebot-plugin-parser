import asyncio
from pathlib import Path

import aiofiles
from httpx import HTTPError, AsyncClient
from nonebot import logger
from tqdm.asyncio import tqdm

from .task import auto_task
from ..utils import merge_av, safe_unlink, generate_file_name
from ..config import pconfig
from ..constants import COMMON_HEADER, DOWNLOAD_TIMEOUT
from ..exception import DownloadException, ZeroSizeException, SizeLimitException

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False


class StreamDownloader:
    """Downloader class for downloading files with stream"""

    def __init__(self):
        self.headers: dict[str, str] = COMMON_HEADER.copy()
        self.cache_dir: Path = pconfig.cache_dir
        self.client: AsyncClient = AsyncClient(timeout=DOWNLOAD_TIMEOUT, verify=False)

    @auto_task
    async def streamd(
        self,
        url: str,
        *,
        file_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download file by url with stream

        Args:
            url (str): url address
            file_name (str | None): file name. Defaults to generate_file_name.
            ext_headers (dict[str, str] | None): ext headers. Defaults to None.

        Returns:
            Path: file path

        Raises:
            httpx.HTTPError: When download fails
        """

        if not file_name:
            file_name = generate_file_name(url)
        file_path = self.cache_dir / file_name

        # 检查文件是否已存在
        if file_path.exists():
            # 简单校验一下大小，防止之前留下了0字节空文件
            if file_path.stat().st_size > 0:
                return file_path
            else:
                await safe_unlink(file_path)

        # ================== 针对 NGA 使用 curl_cffi 下载 ==================
        if "nga.178.com" in url and CURL_CFFI_AVAILABLE:
            logger.info(f"检测到 NGA 图片，使用 curl_cffi 下载: {url}")

            try:
                # 在线程池中执行同步的 curl_cffi 请求
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    self._download_with_curl_cffi,
                    url,
                    file_path,
                    file_name,
                )

                if file_path.exists() and file_path.stat().st_size > 0:
                    logger.success(f"curl_cffi 下载成功: {file_path}")
                    return file_path
                else:
                    await safe_unlink(file_path)
                    raise DownloadException("NGA 图片下载失败 (curl_cffi)")

            except Exception as e:
                logger.exception(f"curl_cffi 下载异常: {e}")
                await safe_unlink(file_path)
                # 回退到普通 httpx 下载
                logger.info("回退到 httpx 下载")
        # ====================================================================

        # === 以下是原有的 httpx 下载逻辑 ===
        headers = {**self.headers, **(ext_headers or {})}

        try:
            async with self.client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                content_length = int(content_length) if content_length else 0

                if content_length == 0:
                    # 有些服务器不返回 Content-Length，尝试读取流
                    pass

                if content_length > 0 and (file_size := content_length / 1024 / 1024) > pconfig.max_size:
                    logger.warning(f"媒体 url: {url} 大小 {file_size:.2f} MB 超过 {pconfig.max_size} MB, 取消下载")
                    raise SizeLimitException

                with self.get_progress_bar(file_name, content_length) as bar:
                    async with aiofiles.open(file_path, "wb") as file:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            await file.write(chunk)
                            bar.update(len(chunk))

        except HTTPError:
            await safe_unlink(file_path)
            logger.exception(f"下载失败 | url: {url}, file_path: {file_path}")
            raise DownloadException("媒体下载失败")
        return file_path

    def _download_with_curl_cffi(self, url: str, file_path: Path, file_name: str) -> None:
        """使用 curl_cffi 下载文件（同步方法）

        Args:
            url: 下载地址
            file_path: 保存路径
            file_name: 文件名（用于进度条）
        """
        # 使用 Chrome 的 TLS 指纹来绕过检测
        headers = {
            "Referer": "https://bbs.nga.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        response = curl_requests.get(
            url,
            headers=headers,
            impersonate="chrome110",  # 模拟 Chrome 110 浏览器
            timeout=30,
            stream=True,
        )

        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        content_length = int(content_length) if content_length else 0

        if content_length > 0 and (file_size := content_length / 1024 / 1024) > pconfig.max_size:
            logger.warning(f"媒体 url: {url} 大小 {file_size:.2f} MB 超过 {pconfig.max_size} MB, 取消下载")
            raise SizeLimitException

        # 使用 tqdm 显示进度条（同步版本）
        with tqdm(
            total=content_length,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
            colour="green",
            desc=file_name,
        ) as bar:
            with open(file_path, "wb") as file:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        file.write(chunk)
                        bar.update(len(chunk))

    @staticmethod
    def get_progress_bar(desc: str, total: int | None = None) -> tqdm:
        """获取进度条 bar

        Args:
            desc (str): 描述
            total (int | None): 总大小. Defaults to None.

        Returns:
            tqdm: 进度条
        """
        return tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
            colour="green",
            desc=desc,
        )

    @auto_task
    async def download_video(
        self,
        url: str,
        *,
        video_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download video file by url with stream

        Args:
            url (str): url address
            video_name (str | None): video name. Defaults to get name by parse url.
            ext_headers (dict[str, str] | None): ext headers. Defaults to None.

        Returns:
            Path: video file path

        Raises:
            httpx.HTTPError: When download fails
        """
        if video_name is None:
            video_name = generate_file_name(url, ".mp4")
        return await self.streamd(url, file_name=video_name, ext_headers=ext_headers)

    @auto_task
    async def download_audio(
        self,
        url: str,
        *,
        audio_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download audio file by url with stream

        Args:
            url (str): url address
            audio_name (str | None ): audio name. Defaults to generate from url.
            ext_headers (dict[str, str] | None): ext headers. Defaults to None.

        Returns:
            Path: audio file path

        Raises:
            httpx.HTTPError: When download fails
        """
        if audio_name is None:
            audio_name = generate_file_name(url, ".mp3")
        return await self.streamd(url, file_name=audio_name, ext_headers=ext_headers)

    @auto_task
    async def download_img(
        self,
        url: str,
        *,
        img_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
    ) -> Path:
        """download image file by url with stream

        Args:
            url (str): url
            img_name (str | None): image name. Defaults to generate from url.
            ext_headers (dict[str, str] | None): ext headers. Defaults to None.

        Returns:
            Path: image file path

        Raises:
            httpx.HTTPError: When download fails
        """
        if img_name is None:
            img_name = generate_file_name(url, ".jpg")
        return await self.streamd(url, file_name=img_name, ext_headers=ext_headers)

    async def download_imgs_without_raise(
        self,
        urls: list[str],
        *,
        ext_headers: dict[str, str] | None = None,
    ) -> list[Path]:
        """download images without raise

        Args:
            urls (list[str]): urls
            ext_headers (dict[str, str] | None): ext headers. Defaults to None.

        Returns:
            list[Path]: image file paths
        """
        paths_or_errs = await asyncio.gather(
            *[self.download_img(url, ext_headers=ext_headers) for url in urls],
            return_exceptions=True,
        )
        return [p for p in paths_or_errs if isinstance(p, Path)]

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


DOWNLOADER: StreamDownloader = StreamDownloader()

try:
    import yt_dlp as yt_dlp

    from .ytdlp import YtdlpDownloader

    YTDLP_DOWNLOADER = YtdlpDownloader()
except ImportError:
    YTDLP_DOWNLOADER = None
