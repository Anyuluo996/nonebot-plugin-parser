import os
import asyncio
from uuid import uuid4
from pathlib import Path
from functools import partial
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import aiofiles
from httpx import Proxy, HTTPError, AsyncClient, HTTPStatusError
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

# curl_cffi 的 timeout 只认 tuple[int, int] / int / float。
# 直接传 httpx.Timeout 对象会被 set_curl_options 忽略 → 不设任何超时 → 卡死 CDN 永久挂起。
# 这里用 (connect, read) tuple，与 DOWNLOAD_TIMEOUT 语义对齐。
CURL_DOWNLOAD_TIMEOUT: tuple[float, float] = (DOWNLOAD_TIMEOUT.connect or 15.0, DOWNLOAD_TIMEOUT.read or 60.0)

# Referer 白名单：域名 → Referer 值
_REFERRER_MAP: dict[str, str] = {
    "img.nga.cn": "https://nga.178.com/",
}

# 单次解析内并发下载上限。贴吧/NGA 长帖可能一次解析几十张内嵌图,
# 无限流会瞬间对同一域名打几十个并发 HTTP, 触发平台限流(567)并占满 fd。
# 单线程 asyncio 下 Semaphore 不需要锁; auto_task 创建的下载协程都会经过它。
_DOWNLOAD_SEM = asyncio.Semaphore(8)


# curl 专用域名（httpx 会被检测拦截或受代理环境变量影响导致 TLS 握手失败）
_CURL_ONLY_DOMAINS: frozenset[str] = frozenset(
    {
        "img.nga.cn",
        # 抖音视频播放域名（重定向至 CDN）
        "snssdk.com",
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
        # 抖音伪装域名 CDN：真实 CDN 标识编码进子域/路径，主域固定为 qrstuvwxyzab.com
        # (例: 24098c3c3f....qrstuvwxyzab.com/v5-se-qn-daily-cm.douyinvod.com/...)
        "qrstuvwxyzab.com",
    }
)

# 抖音相关 CDN 域名（国内 CDN，走代理反而容易 TLS 握手失败）。
# 默认这些域名直连；当配置 parser_douyin_cdn_via_proxy=True 时改走 parser_proxy
# （适用于直连抖音 CDN 不通的部署环境）。将来若有非抖音的直连域名，加到
# _NO_PROXY_ALWAYS 即可，_bypass_proxy 会自动让它们始终直连。
_DOUYIN_CDN_DOMAINS: frozenset[str] = frozenset(
    {
        "snssdk.com",
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
        "qrstuvwxyzab.com",
    }
)

# 默认不走代理的域名（当前仅抖音 CDN）。单一数据源，避免维护两份相同列表。
_NO_PROXY_DOMAINS: frozenset[str] = _DOUYIN_CDN_DOMAINS

# 即使 parser_douyin_cdn_via_proxy=True 也始终直连的域名（非抖音）。
# 当前为空；将来若有非抖音的直连白名单域名，加到这里即可，_bypass_proxy 会自动生效。
_NO_PROXY_ALWAYS: frozenset[str] = frozenset()
_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


class _RetryDownload(Exception):
    """Signal that the current request should be retried."""


# 明确不可重试的 HTTP 状态（鉴权/资源不存在/客户端错误）：重试或轮换 CDN 都不会变好
_PERMANENT_HTTP_STATUSES: frozenset[int] = frozenset({400, 401, 404, 405, 410, 451})


def _temp_path(final: Path) -> Path:
    """为最终缓存文件生成同目录唯一临时文件名。

    写临时文件 + ``os.replace`` 发布, 是并发下载同一 URL（两个群同时发同一链接,
    结果缓存要等发送完才写入）不互相截断的关键: 各协程写各自的临时文件,
    发布是原子的, 最终路径任何时刻都是某个完整文件。

    后缀保留最终文件后缀且置于末尾（``.part.mp4`` 而不是 ``.mp4.part``）:
    ffmpeg 按最后一个后缀猜容器格式, 以 ``.part`` 结尾会被判定为未知格式。
    """
    return final.with_name(f".{final.stem}.{uuid4().hex[:8]}.part{final.suffix}")


def _publish(tmp: Path, final: Path) -> Path:
    """把临时文件原子发布为最终缓存文件（同一文件系统内 ``os.replace`` 是原子的）。"""
    os.replace(tmp, final)
    return final


class _FollowRedirect(Exception):
    """Signal that the current request should continue with a redirect target."""

    def __init__(self, location: str):
        self.location = location


def _extract_host(url: str) -> str | None:
    """从 URL 中提取主机名（去掉端口），解析失败返回 None。"""
    try:
        return urlparse(url).netloc.rsplit(":", 1)[0]
    except Exception:
        return None


def _match_domain(url: str, domain_set: frozenset[str]) -> bool:
    """判断 URL 的主机名是否命中域名集合（精确匹配或为其子域名）。"""
    netloc = _extract_host(url)
    if netloc is None:
        return False
    return any(netloc == d or netloc.endswith("." + d) for d in domain_set)


def _auto_referer(url: str) -> str | None:
    """根据 URL 域名返回应使用的 Referer，不在白名单中返回 None"""
    netloc = _extract_host(url)
    if netloc is None:
        return None
    return _REFERRER_MAP.get(netloc)


def _use_curl(url: str) -> bool:
    """判断该 URL 是否走 curl 下载"""
    return _match_domain(url, _CURL_ONLY_DOMAINS)


def _bypass_proxy(url: str) -> bool:
    """判断该 URL 是否应绕过代理（抖音等国内 CDN）

    当配置 ``parser_douyin_cdn_via_proxy=True`` 时，抖音 CDN 域名不再绕过代理，
    改走 ``parser_proxy``（适用于直连抖音 CDN 不通的部署环境）。
    """
    if pconfig.douyin_cdn_via_proxy:
        # 抖音 CDN 改走代理：仅始终直连白名单（非抖音）保留直连
        return _match_domain(url, _NO_PROXY_ALWAYS)
    return _match_domain(url, _NO_PROXY_DOMAINS)


async def _download_by_curl(
    url: str,
    file_path: Path,
    headers: dict[str, str],
    max_retries: int = 3,
    *,
    backup_urls: list[str] | None = None,
) -> Path:
    """使用 curl_cffi 下载文件（模拟浏览器绕过检测），支持重试

    backup_urls: 备用链接, 重试时轮换不同 CDN。
    """
    from curl_cffi.const import CurlOpt, CurlIpResolve
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
        # 重试时轮换 backup_urls, 避免反复命中同一个坏 CDN 节点
        if attempt == 0 or not backup_urls:
            current_url = url
        else:
            current_url = backup_urls[(attempt - 1) % len(backup_urls)]
            logger.info("curl 重试切换 CDN ({}/{}) | cdn: {}", attempt + 1, max_retries + 1, current_url[:70])
        try:
            # curl_cffi 加超时避免连接挂起永久卡死下载。
            # 注意: curl_cffi 的 set_curl_options 只认 tuple/int/float, 传 httpx.Timeout
            # 会被忽略导致无超时, 故用 CURL_DOWNLOAD_TIMEOUT (connect, read) tuple。
            async with AsyncSession(
                impersonate=impersonate,
                timeout=CURL_DOWNLOAD_TIMEOUT,
                # 强制 IPv4：部分部署环境无 IPv6 出口但 DNS 返回 AAAA 记录，
                # curl 默认优先尝试 IPv6 会先踩坑（超时后回退 IPv4，徒增延迟甚至 reset）。
                # 实测对走代理路径无影响，纯兜底。
                curl_options={CurlOpt.IPRESOLVE: CurlIpResolve.V4},
            ) as session:
                # 流式下载：边读边累计大小，超限立即中止，避免把整个大文件载入内存
                resp = await session.get(
                    current_url,
                    headers=headers,
                    allow_redirects=True,
                    proxies=proxies,  # type: ignore[arg-type]
                    stream=True,
                )
                status = resp.status_code

                if status == 567:
                    raise _RetryDownload("567 频率限制")

                if status != 200:
                    # 非 200 不再直接判死: 抖音 CDN 单节点的 403/5xx 多为瞬时问题,
                    # 按重试预算轮换 backup_urls 往往能救回 (与 httpx 路径对称)。
                    # 只有确定性失败(400/401/404/...)才直接判定失败。
                    if status in _PERMANENT_HTTP_STATUSES:
                        await safe_unlink(file_path)
                        logger.error("curl_cffi 下载失败 HTTP {} | url: {}", status, current_url)
                        raise DownloadException("媒体下载失败")
                    raise _RetryDownload(f"HTTP {status}")

                # 流式写盘 + 大小上限校验（修复：原先 len(resp.content) 已把整文件载入内存）
                downloaded_size = 0
                exceed_size_limit = False
                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in resp.aiter_content():
                        if not chunk:
                            continue
                        downloaded_size += len(chunk)
                        if downloaded_size > max_size_bytes:
                            exceed_size_limit = True
                            break
                        await f.write(chunk)

                if exceed_size_limit:
                    await safe_unlink(file_path)
                    logger.warning(
                        "媒体 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                        url,
                        downloaded_size / 1024 / 1024,
                        pconfig.max_size,
                    )
                    raise IgnoreException

                if downloaded_size == 0:
                    await safe_unlink(file_path)
                    logger.warning("媒体 url: {}, 大小为 0, 取消下载", current_url)
                    raise IgnoreException

                logger.debug(f"下载完成(curl): {file_path.name} ({downloaded_size / 1024:.0f} KB)")
                return file_path

        except (IgnoreException, DownloadException):
            # 语义明确的业务异常：不应被重试，直接上抛
            raise
        except _RetryDownload as e:
            reason = str(e) or "需要重试"
            if attempt == max_retries:
                await safe_unlink(file_path)
                logger.error("curl 重试耗尽({}) | url: {}", reason, current_url)
                raise DownloadException("媒体下载失败")
            wait = 2**attempt
            logger.warning(
                "curl 请求需重试({}), {}s 后重试 ({}/{}) | url: {}",
                reason,
                wait,
                attempt + 1,
                max_retries,
                current_url,
            )
            await asyncio.sleep(wait)
            continue
        except Exception as e:
            if attempt == max_retries:
                await safe_unlink(file_path)
                logger.exception("curl_cffi 下载异常 | url: {}", current_url)
                raise DownloadException("媒体下载失败")
            wait = 2**attempt
            logger.warning(
                "curl_cffi 下载异常: {}, {}s 后重试 ({}/{}) | url: {}",
                e,
                wait,
                attempt + 1,
                max_retries,
                current_url,
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
        self.client: AsyncClient = AsyncClient(timeout=DOWNLOAD_TIMEOUT, verify=pconfig.verify_ssl, proxy=proxy)
        self.direct_client: AsyncClient = AsyncClient(
            timeout=DOWNLOAD_TIMEOUT,
            verify=pconfig.verify_ssl,
            trust_env=False,
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
        backup_urls: list[str] | None = None,
    ) -> Path:
        """download file by url with stream

        backup_urls: 备用 CDN 链接列表。重试时轮换不同链接,
        避免反复命中同一个坏节点(如 B 站 mcdn P2P 节点)。为空则重试同一个 url。

        注意: _use_curl / _bypass_proxy 按 ``url`` (主链) 判定 client 和代理配置,
        backup_urls 应与主链属于同一 curl/代理类别(同平台 CDN 总是如此)。
        若 backup 跨类别(如主链 bilivideo、备选 snssdk), 会在重试时用错 client。
        """
        if not file_name:
            file_name = generate_file_name(url)
        file_path = self.cache_dir / file_name
        if file_path.exists():
            if file_path.stat().st_size == 0:
                # 历史遗留的 0 字节缓存（下载中途被取消时 aiofiles 已建文件但未写入），
                # 视为未下载重新拉取，否则坏缓存会被永久命中
                logger.warning("下载缓存存在但为 0 字节, 重新下载: {}", file_path.name)
            else:
                logger.debug(f"下载缓存命中: {file_path.name}")
                return file_path

        headers = {**self.headers, **(ext_headers or {})}
        if "Referer" not in headers:
            if auto_ref := _auto_referer(url):
                headers["Referer"] = auto_ref

        # 写临时文件再原子发布: 同一 URL 被两处并发解析时(结果缓存要等发送完才写),
        # 两个协程各写各的临时文件, 不会以 "wb" 交替截断同一路径; 亦不会出现
        # 「下载到一半的截断文件因 st_size != 0 被永久当有效缓存」。
        tmp_path = _temp_path(file_path)

        use_curl_result = _use_curl(url)
        logger.debug("_use_curl check | url: {}, result: {}", url[:80], use_curl_result)
        if use_curl_result:
            tmp_path = await _download_by_curl(url, tmp_path, headers, max_retries, backup_urls=backup_urls)
            return _publish(tmp_path, file_path)

        client = self.direct_client if _bypass_proxy(url) else self.client
        max_size_bytes = pconfig.max_size * 1024 * 1024
        max_redirects = 10

        for attempt in range(max_retries + 1):
            redirect_count = 0
            # 重试时轮换 backup_urls, 避免反复命中同一个坏 CDN 节点
            if attempt == 0 or not backup_urls:
                current_url = url
            else:
                current_url = backup_urls[(attempt - 1) % len(backup_urls)]
                logger.info("重试切换 CDN ({}/{}) | cdn: {}", attempt + 1, max_retries + 1, current_url[:70])
            try:
                while True:
                    try:
                        async with client.stream(
                            "GET",
                            current_url,
                            headers=headers,
                            follow_redirects=False,
                        ) as response:
                            status = response.status_code

                            if status == 567:
                                if attempt == max_retries:
                                    await safe_unlink(tmp_path)
                                    logger.error("567 重试耗尽 | url: {}", current_url)
                                    raise DownloadException("媒体下载失败")
                                raise _RetryDownload

                            if status in _REDIRECT_STATUSES:
                                redirect_url = response.headers.get("Location")
                                if redirect_url:
                                    redirect_count += 1
                                    if redirect_count > max_redirects:
                                        await safe_unlink(tmp_path)
                                        logger.error(
                                            "重定向次数超过上限 {} | url: {}",
                                            max_redirects,
                                            current_url,
                                        )
                                        raise DownloadException("媒体下载失败")
                                    raise _FollowRedirect(urljoin(current_url, redirect_url))

                            if status != 200:
                                # 排空错误响应体, 避免连接池复用未读完的 keepalive 连接,
                                # 在下一个并发下载时触发 ResponseNotRead。
                                await response.aread()
                                response.raise_for_status()

                            content_length_header = response.headers.get("Content-Length")
                            try:
                                content_length = int(content_length_header) if content_length_header else None
                            except ValueError:
                                content_length = None

                            if content_length == 0:
                                logger.warning(
                                    "媒体 url: {}, 大小为 0, 取消下载",
                                    current_url,
                                )
                                raise IgnoreException

                            if (
                                content_length is not None
                                and (file_size := content_length / 1024 / 1024) > pconfig.max_size
                            ):
                                logger.warning(
                                    "媒体 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                                    current_url,
                                    file_size,
                                    pconfig.max_size,
                                )
                                raise IgnoreException

                            downloaded_size = 0
                            exceed_size_limit = False
                            with self.rich_progress(
                                file_name,
                                content_length,
                            ) as update_progress:
                                async with aiofiles.open(tmp_path, "wb") as file:
                                    async for chunk in response.aiter_bytes(chunk_size):
                                        if not chunk:
                                            continue

                                        downloaded_size += len(chunk)
                                        # 累计校验对压缩响应同样生效: Content-Length 是
                                        # 传输编码后长度, aiter_bytes 返回解压后字节,
                                        # 仅靠长度预检会让 gzip 膨胀响应绕过磁盘上限。
                                        if downloaded_size > max_size_bytes:
                                            exceed_size_limit = True
                                            break

                                        await file.write(chunk)
                                        update_progress(advance=len(chunk))

                            if exceed_size_limit:
                                await safe_unlink(tmp_path)
                                file_size = downloaded_size / 1024 / 1024
                                logger.warning(
                                    "媒体 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                                    current_url,
                                    file_size,
                                    pconfig.max_size,
                                )
                                raise IgnoreException

                            if downloaded_size == 0:
                                await safe_unlink(tmp_path)
                                logger.warning(
                                    "媒体 url: {}, 大小为 0, 取消下载",
                                    current_url,
                                )
                                raise IgnoreException

                            logger.debug(f"下载完成: {file_path.name} ({downloaded_size / 1024:.0f} KB)")
                            return _publish(tmp_path, file_path)
                    except _FollowRedirect as redirect:
                        current_url = redirect.location
                        continue
            except _RetryDownload:
                wait = 2**attempt
                logger.warning(
                    "媒体服务器返回 567 (疑似频率限制), {}s 后重试 ({}/{}) | url: {}",
                    wait,
                    attempt + 1,
                    max_retries,
                    current_url,
                )
                await asyncio.sleep(wait)
                continue

            except HTTPError as e:
                # HTTP 状态错误带状态码, 传输层错误(超时/连错)带异常类名, 便于排查
                reason = f"HTTP {e.response.status_code}" if isinstance(e, HTTPStatusError) else type(e).__name__
                if attempt == max_retries:
                    await safe_unlink(tmp_path)
                    logger.exception(
                        "下载失败 {} | url: {}, file_path: {}",
                        reason,
                        current_url,
                        file_path,
                    )
                    raise DownloadException("媒体下载失败")
                wait = 2**attempt
                logger.warning(
                    "下载异常 {}, {}s 后重试 ({}/{}) | url: {}",
                    reason,
                    wait,
                    attempt + 1,
                    max_retries,
                    current_url,
                )
                await asyncio.sleep(wait)

        # 循环出口只可能是重试预算耗尽: 不能返回不存在的临时/最终文件
        await safe_unlink(tmp_path)
        raise DownloadException("媒体下载失败")

    @auto_task
    async def download_video(
        self,
        url: str,
        *,
        video_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
        backup_urls: list[str] | None = None,
    ) -> Path:
        """download video file by url with stream"""
        if video_name is None:
            video_name = generate_file_name(url, ".mp4")
        return await self.download_file(
            url, file_name=video_name, ext_headers=ext_headers, chunk_size=1024 * 1024, backup_urls=backup_urls
        )

    @auto_task
    async def download_audio(
        self,
        url: str,
        *,
        audio_name: str | None = None,
        ext_headers: dict[str, str] | None = None,
        backup_urls: list[str] | None = None,
    ) -> Path:
        """download audio file by url with stream"""
        if audio_name is None:
            audio_name = generate_file_name(url, ".mp3")
        return await self.download_file(url, file_name=audio_name, ext_headers=ext_headers, backup_urls=backup_urls)

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
        v_backup_urls: list[str] | None = None,
        a_backup_urls: list[str] | None = None,
    ) -> Path:
        """download video and audio file by url with stream and merge

        v_backup_urls / a_backup_urls: 视频和音频各自的备用 CDN 链接,
        重试时独立轮换(互不干扰)。
        """
        v_path, a_path = await asyncio.gather(
            self.download_video(v_url, ext_headers=ext_headers, backup_urls=v_backup_urls),
            self.download_audio(a_url, ext_headers=ext_headers, backup_urls=a_backup_urls),
        )
        if output_path.exists() and output_path.stat().st_size > 0:
            logger.debug(f"合并产物缓存命中: {output_path.name}")
            return output_path
        # 合并产物同样临时文件 + 原子发布: 两处并发解析同一视频时, 两路 ffmpeg
        # 不会同时写同一输出文件（并发写会得到无法播放的坏文件且被永久缓存）
        tmp_path = _temp_path(output_path)
        try:
            await merge_av(v_path=v_path, a_path=a_path, output_path=tmp_path)
        except BaseException:
            await safe_unlink(tmp_path)
            raise
        return _publish(tmp_path, output_path)

    async def download_imgs_without_raise(
        self,
        urls: list[str],
        *,
        ext_headers: dict[str, str] | None = None,
    ) -> list[Path]:
        """download image files by urls with stream, ignore errors

        并发受 ``_DOWNLOAD_SEM`` 限制（默认 8），避免一次解析几十张图时
        瞬间打爆目标域名 / 占满文件描述符。
        """

        async def _one(url: str) -> Path:
            async with _DOWNLOAD_SEM:
                return await self.download_img(url, ext_headers=ext_headers)

        paths_or_errs = await asyncio.gather(*[_one(url) for url in urls], return_exceptions=True)
        paths: list[Path] = []
        for url, item in zip(urls, paths_or_errs):
            if isinstance(item, Path):
                paths.append(item)
                continue
            # 静默丢弃会让「整贴图床 403」只剩卡片少图、日志无线索
            logger.warning(f"图片下载失败, 跳过该张 | url: {url[:100]} - {type(item).__name__}: {item}")
        return paths

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
        if video_path.exists() and video_path.stat().st_size > 0:
            logger.debug(f"m3u8 缓存命中: {video_path.name}")
            return video_path

        max_size_bytes = pconfig.max_size * 1024 * 1024
        slice_headers = ext_headers or {}
        # 与 download_file 同策略: 临时文件 + 原子发布, 避免并发解析同一链接时
        # 以 "wb" 截断正在发送/下载的同一路径文件
        tmp_path = _temp_path(video_path)

        try:
            async with aiofiles.open(tmp_path, "wb") as f:
                total_size = 0
                exceed_size_limit = False
                with self.rich_progress(desc=video_name) as update_progress:
                    for url in await self._get_m3u8_slices(m3u8_url):
                        slice_client = self.direct_client if _bypass_proxy(url) else self.client
                        async with slice_client.stream("GET", url, headers=slice_headers) as response:
                            # 分片同样要判状态码: 过期/限流分片返回 403 时, 错误页会被
                            # 原样拼进 mp4, 产出无法播放的视频且日志无任何线索
                            response.raise_for_status()
                            async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                                total_size += len(chunk)
                                # 大小上限校验：构造/被劫持的 m3u8 可让 bot 无限写盘到磁盘满
                                if total_size > max_size_bytes:
                                    exceed_size_limit = True
                                    break
                                await f.write(chunk)
                                update_progress(advance=len(chunk), total=total_size)
                        if exceed_size_limit:
                            break
        except HTTPError:
            await safe_unlink(tmp_path)
            logger.exception("m3u8 视频下载失败")
            raise DownloadException("m3u8 视频下载失败")
        except BaseException:
            # 取消/磁盘写入异常同样清理临时文件, 不留半截 .part
            await safe_unlink(tmp_path)
            raise

        if exceed_size_limit:
            await safe_unlink(tmp_path)
            logger.warning(
                "m3u8 视频 url: {} 大小 {:.2f} MB, 超过 {} MB, 取消下载",
                m3u8_url,
                total_size / 1024 / 1024,
                pconfig.max_size,
            )
            raise IgnoreException

        return _publish(tmp_path, video_path)

    async def _get_m3u8_slices(self, m3u8_url: str):
        """获取 m3u8 分片"""

        m3u8_client = self.direct_client if _bypass_proxy(m3u8_url) else self.client
        async with m3u8_client.stream("GET", m3u8_url) as response:
            response.raise_for_status()
            # stream 模式下访问 .text 必须先 aread() 读取响应体, 否则抛
            # ResponseNotRead (m3u8 文本很小, aread 安全)。
            await response.aread()
            slices_text = response.text

        slices: list[str] = []

        for line in slices_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slices.append(urljoin(m3u8_url, line))

        return slices


DOWNLOADER: StreamDownloader = StreamDownloader()

# tdl (Telegram Downloader) 辅助函数：按需调用，二进制不可用时不影响导入
from .tdl import (
    LoginQrHandle as LoginQrHandle,
)
from .tdl import (
    login_qr as login_qr,
)
from .tdl import (
    download_media as download_media,
)
from .tdl import (
    fetch_messages as fetch_messages,
)
from .tdl import (
    start_login_qr as start_login_qr,
)
from .tdl import (
    classify_by_ext as classify_by_ext,
)
from .tdl import (
    extract_qr_ascii as extract_qr_ascii,
)
from .tdl import (
    is_tdl_available as is_tdl_available,
)
from .tdl import (
    submit_2fa_password as submit_2fa_password,
)
from .tdl import (
    wait_login_complete as wait_login_complete,
)

try:
    import yt_dlp as yt_dlp

    from .ytdlp import YtdlpDownloader

    YTDLP_DOWNLOADER = YtdlpDownloader()
except ImportError:
    YTDLP_DOWNLOADER = None
