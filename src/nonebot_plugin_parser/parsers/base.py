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
from ..exception import TipException as TipException
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


async def _is_platform_allowed(platform_name: str) -> bool:
    """跨 parser 路由时复用 matcher 层的群组平台禁用检查。

    短链重定向可能落到管理员已关闭解析的平台（如 v.douyin.com → 汽水音乐，
    而汽水已被该群关闭）。此时应跳过目标 parser，与用户直接发该平台链接时
    matcher 层 ``is_platform_enabled`` 的判定保持一致，避免短链绕过关闭设定。

    通过 nonebot 的 ``current_bot`` / ``current_event`` ContextVar 拿运行态上下文
    构造 Session（仅在 matcher 运行上下文中可用）。拿不到上下文时（如测试、
    非消息触发的调用）返回 True 放行——matcher 层缺 session 也不阻断解析。

    仅检查群组级平台开关；黑名单 / Telegram 授权 / 强制解析授权等用户级检查
    属于 matcher 职责，不在此重复。
    """
    try:
        from nonebot.matcher import current_bot, current_event
        from nonebot_plugin_uninfo import get_session

        bot = current_bot.get()
        event = current_event.get()
    except LookupError:
        # 不在 matcher 运行上下文（如测试直调），放行
        return True

    # get_session 会调适配器接口（如 OneBot get_group_info），可能抛 I/O 异常。
    # 鉴权失败不应阻断解析——降级放行，与 matcher 层缺 session 行为一致。
    try:
        session = await get_session(bot, event)
    except Exception:
        from nonebot import logger

        logger.debug(
            f"_is_platform_allowed: get_session 失败, 放行 {platform_name}",
            exc_info=True,
        )
        return True

    if session is None:
        return True

    from ..matchers.filter import is_platform_enabled

    return is_platform_enabled(session, platform_name)


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
        # 跳过抽象类（显式继承 ABC）或自身定义了 _abstract_parser 的中间基类
        # 用 cls.__dict__ 判断：只认类自身定义的，继承的不算（子类不被跳过）
        is_abstract = ABC in cls.__bases__ or "_abstract_parser" in cls.__dict__
        if not is_abstract:
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
        from nonebot import logger

        url = searched.group(0)
        logger.debug(f"[{self.platform.display_name}] 开始解析: {url[:80]}")
        result = await self._handlers[keyword](self, searched)
        logger.debug(
            f"[{self.platform.display_name}] 解析完成: "
            f"type={result.content_type}, contents={len(result.contents)}, "
            f"title={result.title!r:.40}"
        )
        return result

    @final
    async def parse_with_redirect(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> ParseResult:
        """先重定向再解析。

        短链 (如 v.douyin.com) 重定向后的真实 URL 可能落到别的平台 (如汽水音乐
        music.douyin.com)。若本 parser 匹配不到，遍历已注册 parser 找能匹配的
        转发解析，实现短链跨平台路由。适用于所有短链 parser (lofter/weibo/xhs 等)。

        跨 parser 路由会复用 matcher 层的群组平台禁用检查 (is_platform_enabled)，
        避免短链重定向绕过管理员关闭某平台的设定（如关了汽水但抖音短链 redirect
        到汽水）。黑名单 / Telegram 授权等用户级检查仍在 matcher 层，不在此重复。
        """
        from nonebot import logger

        redirect_url = await self.get_redirect_url(url, headers=headers or self.headers)

        if redirect_url == url:
            raise ParseException(f"无法重定向 URL: {url}")

        logger.info(f"URL 重定向: {url} -> {redirect_url}")

        try:
            keyword, searched = self.search_url(redirect_url)
        except ParseException:
            # 本 parser 匹配不到: 短链重定向到了其它平台, 尝试跨 parser 路由。
            # 懒导入避免 parsers -> matchers 的循环导入 (matchers 顶层依赖 parsers)。
            from ..matchers import KEYWORD_PARSER_MAP

            # 按 id 去重: 一个 parser 类注册多个 keyword, map.values() 会重复出现。
            seen: set[int] = set()
            for parser in KEYWORD_PARSER_MAP.values():
                if id(parser) in seen or parser is self:
                    continue
                seen.add(id(parser))
                try:
                    keyword, searched = parser.search_url(redirect_url)
                except ParseException:
                    continue
                # 复用 matcher 层群组平台禁用检查，避免短链 redirect 绕过关闭设定。
                # 拿不到 session (非 matcher 运行上下文) 时放行，与 matcher 层行为一致
                # (matcher 层缺 session 也不阻断)。
                if not await _is_platform_allowed(parser.platform.name):
                    logger.info(
                        f"跨 parser 路由: 目标平台 {parser.platform.display_name} "
                        f"在当前会话已禁用, 跳过: {redirect_url[:80]}"
                    )
                    continue
                logger.info(
                    f"跨 parser 路由: {redirect_url[:80]} -> "
                    f"{parser.platform.display_name}"
                )
                return await parser.parse(keyword, searched)
            raise ParseException(f"无法匹配 {redirect_url}")

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
        trust_env: bool = False,
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
            trust_env: 是否读取环境变量代理等配置，默认 False。

                国内平台（B 站、抖音、微博等）默认直连，避免被容器层 ``HTTP_PROXY`` /
                ``HTTPS_PROXY`` 环境变量错误地经代理（代理通常只面向境外站点）。
                需要走代理的平台（Pixiv/YouTube 等）请通过 ``proxy`` 显式传入，或
                临时传 ``trust_env=True`` 兜底。
            raise_for_status: 是否对 >= 400 的响应抛错，默认 True。
            timeout: 覆盖默认超时。
            proxy: httpx 代理（Proxy 对象或 URL 字符串），默认 None。

                与 ``trust_env`` 独立：传了 ``proxy`` 即走该代理，不受 ``trust_env`` 影响。

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

    async def request_curl(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Any | None = None,
        timeout: float = 15,
        allow_redirects: bool = True,
        verify: bool = False,
        raise_for_status: bool = False,
    ):
        """带 TLS 指纹模拟的 HTTP GET 请求（curl_cffi），不可用时回退 httpx。

        收敛 NGA/酷狗等需绕反爬（SSA TLS 指纹检测）平台重复的 curl_cffi 调用样板。
        与 ``request``（httpx）签名风格一致，返回的 response 接口兼容
        （都有 status_code/text/headers/.json()）。

        代理逻辑：``pconfig.proxy`` 有值则走代理，无值则用空字符串显式禁用
        （避免被容器层 HTTP_PROXY/HTTPS_PROXY 环境变量误伤），与原各 parser 内联逻辑一致。

        Args:
            url: 请求地址。
            headers: 请求头（整体覆盖实例默认 headers）。
            params: 查询参数。
            timeout: 超时秒数。
            allow_redirects: 是否跟随重定向。
            verify: 是否校验 TLS 证书。
            raise_for_status: 是否对 >= 400 的响应抛错。

        Returns:
            curl_cffi.Response 或 httpx.Response（回退时）。
        """
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            AsyncSession = None  # type: ignore[assignment]

        if AsyncSession is not None:
            client_headers = headers if headers is not None else self.headers
            proxy_url = pconfig.proxy
            proxies = {"https": proxy_url, "http": proxy_url} if proxy_url else {"https": "", "http": ""}

            async with AsyncSession(
                impersonate="chrome110",
                timeout=timeout,
                verify=verify,
            ) as session:
                response = await session.get(
                    url,
                    params=params,
                    headers=client_headers,
                    allow_redirects=allow_redirects,
                    proxies=proxies,  # type: ignore[arg-type]
                )
                if raise_for_status and response.status_code >= 400:
                    response.raise_for_status()
                return response

        # curl_cffi 不可用 → 回退 httpx（大概率被指纹检测拦截，保留兜底）
        return await self.request(
            url,
            headers=headers,
            params=params,
            follow_redirects=allow_redirects,
            raise_for_status=raise_for_status,
            timeout=timeout,
        )

    @staticmethod
    async def get_redirect_url(
        url: str,
        headers: dict[str, str] | None = None,
        proxy: Any | None = None,
    ) -> str:
        """获取重定向后的 URL, 单次重定向

        Args:
            proxy: httpx 代理（Proxy 对象或 URL 字符串）。默认 None 直连。
                海外平台（如 TikTok）的短链重定向需显式传入，国内平台无需传。
        """
        from httpx import AsyncClient

        headers = headers or COMMON_HEADER.copy()
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "verify": False,
            "follow_redirects": False,
            "trust_env": False,
            "timeout": COMMON_TIMEOUT,
        }
        if proxy is not None:
            client_kwargs["proxy"] = proxy
        async with AsyncClient(**client_kwargs) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                response.raise_for_status()
            # 重定向响应(3xx)缺 Location 头时, 不应静默返回原 url 让上层抛
            # "无法重定向"（误导）。区分两种情况：3xx 无 Location 是上游异常,
            # 2xx 无 Location 是正常无重定向。
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ParseException(f"重定向响应({response.status_code})缺少 Location 头: {url}")
                return location
            return response.headers.get("Location", url)

    @staticmethod
    async def get_final_url(
        url: str,
        headers: dict[str, str] | None = None,
        proxy: Any | None = None,
    ) -> str:
        """获取重定向后的 URL, 允许多次重定向

        Args:
            proxy: httpx 代理（Proxy 对象或 URL 字符串）。默认 None 直连。
        """
        from httpx import AsyncClient

        headers = headers or COMMON_HEADER.copy()
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "verify": False,
            "follow_redirects": True,
            "trust_env": False,
            "timeout": COMMON_TIMEOUT,
        }
        if proxy is not None:
            client_kwargs["proxy"] = proxy
        async with AsyncClient(**client_kwargs) as client:
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
        url_or_task: str | Task[Path] | Path,
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
        """创建图片内容列表

        单次解析内并发下载受 ``download._DOWNLOAD_SEM`` 限制（默认 8），
        避免长帖几十张图瞬间打爆目标域名。
        """
        import asyncio

        from ..download import _DOWNLOAD_SEM

        async def _download_with_sem(url: str):
            async with _DOWNLOAD_SEM:
                return await DOWNLOADER.download_img(url, ext_headers=self.headers)

        contents: list[ImageContent] = []
        for url in image_urls:
            task = asyncio.create_task(_download_with_sem(url))
            contents.append(ImageContent(task))
        return contents

    def create_image_content(
        self,
        url_or_task: str | Task[Path] | Path,
        alt: str | None = None,
    ):
        """创建图片内容"""
        if isinstance(url_or_task, str):
            url_or_task = DOWNLOADER.download_img(url_or_task, ext_headers=self.headers)

        return ImageContent(url_or_task, alt=alt)

    def create_cover_image_task(self, url: str):
        """创建渲染专用封面下载任务（Task[Path]）。

        返回的 task 供 ParseResult.cover_image 使用：仅渲染卡片时下载、绘制，
        不进入 contents，发送流程不会把它当作图片消息发出。
        """
        return DOWNLOADER.download_img(url, ext_headers=self.headers)

    def create_dynamic_contents(
        self,
        dynamic_urls: list[str],
        convert_to_gif: bool = False,
        cover_url: str | None = None,
        cover_urls: list[str] | None = None,
        bgm_url: str | None = None,
    ):
        """创建动态图片内容列表

        Args:
            dynamic_urls: 动态图片 URL 列表
            convert_to_gif: 是否转换为 GIF，默认 False（仅推特平台使用）
            cover_url: 缩略图 URL，对所有动态内容生效
            cover_urls: 每个动态内容单独的缩略图 URL（与 dynamic_urls 一一对应）
            bgm_url: 背景音乐 URL（抖音实况照片视频轨静音, 下载后合并;
                None 时不合并, 默认 None）
        """
        import asyncio

        from .data import DynamicContent

        if cover_urls is not None and len(cover_urls) != len(dynamic_urls):
            raise ValueError(f"cover_urls 长度({len(cover_urls)}) 与 dynamic_urls 长度({len(dynamic_urls)}) 不一致")

        contents: list[DynamicContent] = []
        for i, url in enumerate(dynamic_urls):
            task = DOWNLOADER.download_video(url, ext_headers=self.headers)

            # 抖音实况照片: 视频轨静音, 下载 BGM 后用 merge task 替换 task,
            # get_path() 直接返回含 BGM 的视频 (与 _convert_to_gif 替换 gif_path 同构)。
            # 已含音轨的实况(含原声)在 _merge_bgm 内部跳过, 不影响其它平台。
            if bgm_url:
                audio_task = DOWNLOADER.download_audio(bgm_url, ext_headers=self.headers)
                task = asyncio.create_task(self._merge_bgm(task, audio_task))

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

    async def _merge_bgm(self, video_task: Task[Path], audio_task: Task[Path]) -> Path:
        """合并实况照片视频与 BGM 音频。

        抖音实况照片(live photo)的视频轨本身静音, BGM 在 aweme_detail.music.play_url。
        本方法下载并合并二者, 输出含 BGM 的 mp4; 已含音轨(部分实况含原声)则跳过。

        Args:
            video_task: 视频下载任务 (静音轨)
            audio_task: BGM 音频下载任务

        Returns:
            合并后的视频路径; 视频已含音轨或 ffmpeg 不可用时返回原视频路径
        """
        from nonebot import logger

        from ..utils import merge_av, has_audio_stream

        video_path = await video_task

        # 已含音轨(部分实况含原声)则跳过, 避免 merge_av 丢失原声
        if await has_audio_stream(video_path):
            logger.debug(f"视频已含音轨, 跳过 BGM 合并: {video_path.name}")
            # BGM 任务已在调度, 取消以释放并发槽位 (Task 已启动不可真正中止,
            # 但丢弃其结果避免无谓的磁盘 IO; download_audio 自带缓存不影响)
            audio_task.cancel()
            return video_path

        audio_path = await audio_task
        output = video_path.with_name(f"{video_path.stem}_bgm.mp4")
        try:
            await merge_av(v_path=video_path, a_path=audio_path, output_path=output)
        except (RuntimeError, FileNotFoundError) as e:
            # ffmpeg 不可用或合并失败: 不阻塞发送, 降级为无声视频
            logger.warning(f"BGM 合并失败, 降级为无声视频: {e!r}")
            return video_path
        logger.debug(f"BGM 合并完成: {output.name}")
        return output

    def create_audio_content(
        self,
        url_or_task: str | Task[Path] | Path,
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
