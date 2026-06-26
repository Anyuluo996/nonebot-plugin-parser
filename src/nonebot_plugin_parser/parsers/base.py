from re import Match, Pattern, compile
from abc import ABC
from typing import TYPE_CHECKING, Any, TypeVar, ClassVar, cast
from asyncio import Task
from pathlib import Path
from collections.abc import Callable, Coroutine
from typing_extensions import Unpack, final

from .data import Platform, ParseResult, ImageContent, ParseResultKwargs
from ..config import pconfig as pconfig
from ..download import DOWNLOADER
from ..constants import IOS_HEADER, COMMON_HEADER, ANDROID_HEADER, COMMON_TIMEOUT
from ..constants import DOWNLOAD_TIMEOUT as DOWNLOAD_TIMEOUT
from ..constants import PlatformEnum as PlatformEnum
from ..exception import ParseException
from ..exception import IgnoreException as IgnoreException
from ..exception import DownloadException as DownloadException

T = TypeVar("T", bound="BaseParser")
HandlerFunc = Callable[[T, Match[str]], Coroutine[Any, Any, ParseResult]]
KeyPatterns = list[tuple[str, Pattern[str]]]

_KEY_PATTERNS = "_key_patterns"


# 注册处理器装饰器
def handle(keyword: str, pattern: str):
    """注册处理器装饰器"""

    def decorator(func: HandlerFunc[T]) -> HandlerFunc[T]:
        if not hasattr(func, _KEY_PATTERNS):
            setattr(func, _KEY_PATTERNS, [])

        key_patterns: KeyPatterns = getattr(func, _KEY_PATTERNS)
        key_patterns.append((keyword, compile(pattern)))

        return func

    return decorator


class BaseParser:
    platform: ClassVar[Platform]
    """ 平台信息（包含名称和显示名称） """

    _registry: ClassVar[list[type["BaseParser"]]] = []
    """ 存储所有已注册的 Parser 类 """

    if TYPE_CHECKING:
        _key_patterns: ClassVar[KeyPatterns]
        _handlers: ClassVar[dict[str, HandlerFunc]]

    def __init__(self):
        self.headers = COMMON_HEADER.copy()
        self.ios_headers = IOS_HEADER.copy()
        self.android_headers = ANDROID_HEADER.copy()
        self.timeout = COMMON_TIMEOUT

    def __init_subclass__(cls, **kwargs):
        """自动注册子类到 _registry"""
        super().__init_subclass__(**kwargs)
        if ABC not in cls.__bases__:  # 跳过抽象类
            BaseParser._registry.append(cls)

        cls._handlers = {}
        cls._key_patterns = []

        # 获取所有被 handle 装饰的方法
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if callable(attr) and hasattr(attr, _KEY_PATTERNS):
                key_patterns: KeyPatterns = getattr(attr, _KEY_PATTERNS)
                handler = cast(HandlerFunc, attr)
                for keyword, pattern in key_patterns:
                    cls._handlers[keyword] = handler
                    cls._key_patterns.append((keyword, pattern))

        # 按关键字长度降序排序
        cls._key_patterns.sort(key=lambda x: -len(x[0]))

    @classmethod
    def get_all_subclass(cls) -> list[type["BaseParser"]]:
        """获取所有已注册的 Parser 类"""
        return cls._registry

    @final
    async def parse(self, keyword: str, searched: Match[str]) -> ParseResult:
        return await self._handlers[keyword](self, searched)

    @final
    async def parse_with_redirect(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> ParseResult:
        """先重定向再解析"""
        from nonebot import logger

        redirect_url = await self.get_redirect_url(url, headers=headers or self.headers)

        if redirect_url == url:
            raise ParseException(f"无法重定向 URL: {url}")

        logger.info(f"URL 重定向: {url} -> {redirect_url}")
        keyword, searched = self.search_url(redirect_url)
        logger.info(f"重定向 URL 匹配到: {keyword}")
        return await self.parse(keyword, searched)

    @classmethod
    def search_url(cls, url: str) -> tuple[str, Match[str]]:
        """搜索 URL 匹配模式"""
        for keyword, pattern in cls._key_patterns:
            if keyword not in url:
                continue
            if searched := pattern.search(url):
                return keyword, searched
        raise ParseException(f"无法匹配 {url}")

    @classmethod
    def result(cls, **kwargs: Unpack[ParseResultKwargs]) -> ParseResult:
        """构建解析结果"""
        return ParseResult(platform=cls.platform, **kwargs)

    async def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: Any | None = None,
        content: str | bytes | None = None,
        data: Any | None = None,
        json: Any | None = None,
        cookies: Any | None = None,
        follow_redirects: bool = False,
        trust_env: bool = True,
        raise_for_status: bool = True,
        timeout: Any | None = None,
        proxy: Any | None = None,
    ):
        """发起 HTTP 请求的统一封装，收敛各 parser 中重复的 ``AsyncClient`` 样板。

        使用实例自身的 headers / timeout 作为默认值（可被 ``headers``/``timeout`` 覆盖）。

        Args:
            url: 请求地址。
            method: HTTP 方法，默认 ``GET``。
            headers: 覆盖默认 headers 的字典（整体覆盖，非合并）。
            params/content/data/json: 透传给 httpx 的请求体/查询参数。
            cookies: 透传给 httpx 的 cookies。
            follow_redirects: 是否跟随重定向，默认 False。
            trust_env: 是否读取环境变量代理等配置，默认 True。
            raise_for_status: 是否对 >= 400 的响应抛错，默认 True。
            timeout: 覆盖默认超时。
            proxy: httpx 代理（Proxy 对象或 URL 字符串），默认 None。

        Returns:
            httpx.Response
        """
        from httpx import AsyncClient

        client_headers = headers if headers is not None else self.headers
        client_timeout = timeout if timeout is not None else self.timeout
        client_kwargs: dict[str, Any] = {
            "headers": client_headers,
            "verify": False,
            "cookies": cookies,
            "follow_redirects": follow_redirects,
            "trust_env": trust_env,
            "timeout": client_timeout,
        }
        if proxy is not None:
            client_kwargs["proxy"] = proxy
        async with AsyncClient(**client_kwargs) as client:
            response = await client.request(
                method,
                url,
                params=params,
                content=content,
                data=data,
                json=json,
            )
            if raise_for_status and response.status_code >= 400:
                response.raise_for_status()
            return response

    @staticmethod
    async def get_redirect_url(
        url: str,
        headers: dict[str, str] | None = None,
    ) -> str:
        """获取重定向后的 URL, 单次重定向"""
        from httpx import AsyncClient

        headers = headers or COMMON_HEADER.copy()
        async with AsyncClient(
            headers=headers,
            verify=False,
            follow_redirects=False,
            timeout=COMMON_TIMEOUT,
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                response.raise_for_status()
            return response.headers.get("Location", url)

    @staticmethod
    async def get_final_url(
        url: str,
        headers: dict[str, str] | None = None,
    ) -> str:
        """获取重定向后的 URL, 允许多次重定向"""
        from httpx import AsyncClient

        headers = headers or COMMON_HEADER.copy()
        async with AsyncClient(
            headers=headers,
            verify=False,
            follow_redirects=True,
            timeout=COMMON_TIMEOUT,
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                response.raise_for_status()
            return str(response.url)

    def create_author(
        self,
        name: str,
        avatar_url: str | None = None,
        description: str | None = None,
        avatar_headers: dict[str, str] | None = None,
    ):
        """创建作者对象"""
        from .data import Author

        avatar_task = None
        if avatar_url:
            ext_headers = avatar_headers or self.headers
            avatar_task = DOWNLOADER.download_img(avatar_url, ext_headers=ext_headers)
        return Author(name=name, avatar=avatar_task, description=description)

    def create_video_content(
        self,
        url_or_task: str | Task[Path],
        cover_url: str | None = None,
        duration: float = 0.0,
    ):
        """创建视频内容"""
        from .data import VideoContent

        cover_task = None
        if cover_url:
            cover_task = DOWNLOADER.download_img(cover_url, ext_headers=self.headers)
        if isinstance(url_or_task, str):
            url_or_task = DOWNLOADER.download_video(url_or_task, ext_headers=self.headers)

        return VideoContent(url_or_task, cover_task, duration)

    def create_image_contents(
        self,
        image_urls: list[str],
    ):
        """创建图片内容列表"""
        contents: list[ImageContent] = []
        for url in image_urls:
            task = DOWNLOADER.download_img(url, ext_headers=self.headers)
            contents.append(ImageContent(task))
        return contents

    def create_image_content(
        self,
        url_or_task: str | Task[Path],
        alt: str | None = None,
    ):
        """创建图片内容"""
        if isinstance(url_or_task, str):
            url_or_task = DOWNLOADER.download_img(url_or_task, ext_headers=self.headers)

        return ImageContent(url_or_task, alt=alt)

    def create_dynamic_contents(
        self,
        dynamic_urls: list[str],
        convert_to_gif: bool = False,
        cover_url: str | None = None,
        cover_urls: list[str] | None = None,
    ):
        """创建动态图片内容列表

        Args:
            dynamic_urls: 动态图片 URL 列表
            convert_to_gif: 是否转换为 GIF，默认 False（仅推特平台使用）
            cover_url: 缩略图 URL，对所有动态内容生效
            cover_urls: 每个动态内容单独的缩略图 URL（与 dynamic_urls 一一对应）
        """
        import asyncio

        from .data import DynamicContent

        if cover_urls is not None and len(cover_urls) != len(dynamic_urls):
            raise ValueError(f"cover_urls 长度({len(cover_urls)}) 与 dynamic_urls 长度({len(dynamic_urls)}) 不一致")

        contents: list[DynamicContent] = []
        for i, url in enumerate(dynamic_urls):
            task = DOWNLOADER.download_video(url, ext_headers=self.headers)

            # 处理缩略图: 优先使用每个视频单独的封面, 其次统一封面
            cover_task = None
            individual_cover = cover_urls[i] if cover_urls else cover_url
            if individual_cover:
                cover_task = DOWNLOADER.download_img(individual_cover, ext_headers=self.headers)

            if convert_to_gif:
                # 创建转换任务（仅在指定时才进行GIF转换）
                convert_task = asyncio.create_task(self._convert_to_gif(task))
                contents.append(DynamicContent(task, gif_path=convert_task, cover=cover_task))
            else:
                contents.append(DynamicContent(task, cover=cover_task))
        return contents

    async def _convert_to_gif(self, video_task: Task[Path]) -> Path:
        """将下载的视频转换为 GIF（如果没有音轨）

        Args:
            video_task: 视频下载任务

        Returns:
            GIF 文件路径
        """
        from ..utils import has_audio_stream, convert_video_to_gif

        # 等待视频下载完成
        video_path = await video_task

        # 检测是否有音频流（有音频流的不是 GIF）
        has_audio = await has_audio_stream(video_path)

        if has_audio:
            # 有音频流，这是普通视频，不转换
            from nonebot import logger

            logger.debug(f"检测到音频流，跳过 GIF 转换: {video_path.name}")
            return video_path

        # 无音频流，转换为 GIF
        from nonebot import logger

        logger.info(f"开始转换视频到 GIF: {video_path.name}")
        return await convert_video_to_gif(video_path, optimize=False)

    def create_audio_content(
        self,
        url_or_task: str | Task[Path],
        duration: float = 0.0,
    ):
        """创建音频内容"""
        from .data import AudioContent

        if isinstance(url_or_task, str):
            url_or_task = DOWNLOADER.download_audio(url_or_task, ext_headers=self.headers)

        return AudioContent(url_or_task, duration)

    def create_empty_graphics(self) -> list[str | ImageContent]:
        """创建空的图片内容列表"""
        return []

    @property
    def downloader(self):
        return DOWNLOADER
