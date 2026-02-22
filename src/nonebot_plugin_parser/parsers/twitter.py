import re
from typing import Any, ClassVar
from itertools import chain

from httpx import AsyncClient
from msgspec import Struct, field
from nonebot import logger
from msgspec.json import Decoder

from .base import BaseParser, PlatformEnum, handle
from .data import Platform, ParseResult, MediaContent
from ..exception import ParseException


class MediaElement(Struct):
    """媒体元素"""
    type: str
    """媒体类型 video/image/gif"""
    url: str
    altText: str | None = None
    thumbnail_url: str | None = None
    duration_millis: int | None = None


class VxTwitterResponse(Struct):
    """vx Twitter API 响应"""
    article: str | None
    date_epoch: int
    fetched_on: int
    likes: int
    text: str
    user_name: str
    """用户昵称（显示名称）"""
    user_screen_name: str
    """用户ID (@username)"""
    user_profile_image_url: str
    """用户头像 URL"""
    qrt: "VxTwitterResponse | None" = None
    """转发信息"""
    qrtURL: str | None = None
    media_extended: list[MediaElement] = field(default_factory=list)
    """扩展媒体信息"""


vx_decoder = Decoder(VxTwitterResponse)


class TwitterParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.TWITTER, display_name="小蓝鸟")

    @handle("x.com", r"x.com/[0-9-a-zA-Z_]{1,20}/status/([0-9]+)")
    async def _parse(self, searched: re.Match[str]) -> ParseResult:
        """解析 Twitter 链接（混合方案）"""
        url = f"https://{searched.group(0)}"

        try:
            # 优先使用 vx Twitter API（获取完整信息）
            logger.debug(f"尝试使用 vx Twitter API 解析: {url}")
            return await self.parse_by_vxapi(url)
        except Exception as e:
            logger.warning(f"vx Twitter API 解析失败，降级到 xdown.app API: {e}")
            # 降级到 xdown.app（保持 GIF 转换功能）
            return await self.parse_by_xdown(url)

    async def parse_by_vxapi(self, url: str) -> ParseResult:
        """使用 vx Twitter API 解析（获取完整用户信息和元数据）

        Args:
            url: Twitter 链接

        Returns:
            ParseResult: 包含完整信息的解析结果
        """
        # API URL 转换: x.com/user/status/123 -> api.vxtwitter.com/user/status/123
        api_url = url.replace("x.com/", "api.vxtwitter.com/")

        # httpx 会自动使用环境变量的代理设置
        async with AsyncClient(headers=self.headers, timeout=self.timeout, verify=False) as client:
            response = await client.get(api_url)
            response.raise_for_status()

        # 解析 JSON 响应
        data: VxTwitterResponse = vx_decoder.decode(response.content)

        # 提取完整的作者信息
        author = self.create_author(
            name=data.user_screen_name,
            avatar_url=data.user_profile_image_url
        )

        # 处理媒体内容
        contents: list[MediaContent] = []
        for media in data.media_extended:
            if media.type in ("video", "gif"):
                # 检测是否为 GIF（tweet_video 是 Twitter 的 GIF 格式）
                is_gif = "tweet_video" in media.url or media.type == "gif"

                if is_gif:
                    # GIF 内容：使用 DynamicContent，保留转换功能并添加缩略图
                    logger.debug(f"检测到 GIF 内容，将转换为 GIF: {media.url}")
                    contents.extend(self.create_dynamic_contents(
                        [media.url],
                        convert_to_gif=True,
                        cover_url=media.thumbnail_url
                    ))
                else:
                    # 普通视频
                    contents.append(self.create_video_content(media.url, cover_url=media.thumbnail_url))
            elif media.type == "image":
                # 图片内容
                contents.extend(self.create_image_contents([media.url]))

        # 处理转发信息
        repost = self._collect_vx_result(data.qrt) if data.qrt else None

        return self.result(
            author=author,
            title=data.article,
            text=data.text,
            timestamp=data.date_epoch,
            contents=contents,
            repost=repost,
        )

    def _collect_vx_result(self, data: VxTwitterResponse) -> ParseResult:
        """递归收集转发信息"""
        author = self.create_author(
            name=data.user_screen_name,
            avatar_url=data.user_profile_image_url
        )

        contents: list[MediaContent] = []
        for media in data.media_extended:
            if media.type in ("video", "gif"):
                is_gif = "tweet_video" in media.url or media.type == "gif"
                if is_gif:
                    logger.debug(f"检测到 GIF 内容，将转换为 GIF: {media.url}")
                    contents.extend(self.create_dynamic_contents(
                        [media.url],
                        convert_to_gif=True,
                        cover_url=media.thumbnail_url
                    ))
                else:
                    contents.append(self.create_video_content(media.url, cover_url=media.thumbnail_url))
            elif media.type == "image":
                contents.extend(self.create_image_contents([media.url]))

        return self.result(
            author=author,
            title=data.article,
            text=data.text,
            timestamp=data.date_epoch,
            contents=contents,
            repost=self._collect_vx_result(data.qrt) if data.qrt else None,
        )

    async def parse_by_xdown(self, url: str) -> ParseResult:
        """使用 xdown.app API 解析（降级方案，保留 GIF 转换）

        Args:
            url: Twitter 链接

        Returns:
            ParseResult: 解析结果
        """
        resp = await self._req_xdown_api(url)
        if resp.get("status") != "ok":
            raise ParseException("xdown.app API 解析失败")

        html_content = resp.get("data")
        if html_content is None:
            raise ParseException("xdown.app API 返回数据为空")

        return self.parse_twitter_html(html_content)

    async def _req_xdown_api(self, url: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://xdown.app",
            "Referer": "https://xdown.app/",
            **self.headers,
        }
        data = {"q": url, "lang": "zh-cn"}

        # 支持代理
        from ..config import pconfig

        proxy = pconfig.proxy if pconfig.proxy else None

        async with AsyncClient(headers=headers, timeout=self.timeout, proxy=proxy) as client:
            api_url = "https://xdown.app/api/ajaxSearch"
            response = await client.post(api_url, data=data)
            return response.json()

    def parse_twitter_html(self, html_content: str) -> ParseResult:
        """解析 Twitter HTML 内容

        Args:
            html_content (str): Twitter HTML 内容

        Returns:
            ParseResult: 解析结果
        """
        from bs4 import Tag, BeautifulSoup

        soup = BeautifulSoup(html_content, "html.parser")

        # 初始化数据
        title = None
        cover_url = None
        video_url = None
        images_urls = []
        dynamic_urls = []
        is_animated_gif = False  # 标记是否为动画 GIF

        # 1. 检查缩略图 URL (tweet_video_thumb 通常是 GIF)
        thumb_tag = soup.find("img")
        if isinstance(thumb_tag, Tag):
            if cover := thumb_tag.get("src"):
                cover_url = str(cover)
                # 检查缩略图 URL 是否包含 tweet_video_thumb
                if "tweet_video_thumb" in cover_url:
                    is_animated_gif = True
                    from nonebot import logger

                    logger.info("检测到 tweet_video_thumb 缩略图，判断为 GIF")

        # 2. 提取下载链接
        tw_button_tags = soup.find_all("a", class_="tw-button-dl")
        abutton_tags = soup.find_all("a", class_="abutton")
        for tag in chain(tw_button_tags, abutton_tags):
            if not isinstance(tag, Tag):
                continue
            href = tag.get("href")
            if href is None:
                continue

            href = str(href)
            text = tag.get_text(strip=True)

            if "下载 MP4" in text:
                video_url = href
                break
            elif "下载图片" in text:
                images_urls.append(href)
            elif "下载 gif" in text or "(gif)" in text.lower():
                dynamic_urls.append(href)
                # 通过下载链接文本确认是 GIF
                is_animated_gif = True
                from nonebot import logger

                logger.info("检测到 '下载 gif' 链接，判断为 GIF")

        # 3. 提取标题
        title_tag = soup.find("h3")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 简洁的构建方式
        contents = []

        # 添加视频内容
        if video_url:
            contents.append(self.create_video_content(video_url, cover_url))

        # 添加图片内容
        if images_urls:
            contents.extend(self.create_image_contents(images_urls))

        # 添加动态内容（如果检测到是 GIF，则转换）
        if dynamic_urls:
            contents.extend(self.create_dynamic_contents(dynamic_urls, convert_to_gif=is_animated_gif))

        return self.result(
            title=title,
            author=self.create_author("无用户名"),
            contents=contents,
        )
        # # 4. 提取Twitter ID
        # twitter_id_input = soup.find("input", {"id": "TwitterId"})
        # if (
        #     twitter_id_input
        #     and isinstance(twitter_id_input, Tag)
        #     and (value := twitter_id_input.get("value"))
        #     and isinstance(value, str)
        # ):
