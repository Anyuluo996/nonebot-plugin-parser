import re
import asyncio
from typing import Any, ClassVar

from httpx import Proxy
from msgspec import Struct
from msgspec.json import Decoder

from .base import BaseParser, handle
from .data import Platform, ParseResult, ImageContent, DynamicContent
from ..config import pconfig as pconfig
from ..constants import PlatformEnum


class PixivIllustResponse(Struct):
    """PixivNow /ajax/illust/{id} 响应"""

    error: bool = False
    body: dict[str, Any] = {}


class PixivPagesResponse(Struct):
    """PixivNow /ajax/illust/{id}/pages 响应"""

    error: bool = False
    body: list[dict[str, Any]] = []


class PixivUgoiraMetaResponse(Struct):
    """PixivNow /ajax/illust/{id}/ugoira_meta 响应"""

    error: bool = False
    body: dict[str, Any] = {}


class PixivUserResponse(Struct):
    """PixivNow /ajax/user/{userId}?full=1 响应"""

    error: bool = False
    body: dict[str, Any] = {}


pages_decoder = Decoder(PixivPagesResponse)
illust_decoder = Decoder(PixivIllustResponse)
ugoira_decoder = Decoder(PixivUgoiraMetaResponse)
user_decoder = Decoder(PixivUserResponse)


class PixivParser(BaseParser):
    """Pixiv 解析器"""

    platform: ClassVar[Platform] = Platform(name=PlatformEnum.PIXIV, display_name="Pixiv")

    @classmethod
    def _get_base_url(cls) -> str:
        """获取 PixivNow API 地址"""
        return pconfig.pixiv

    @classmethod
    def _is_r18_allowed(cls) -> bool:
        """检查是否允许 R18 内容"""
        return pconfig.pixivR18

    @classmethod
    def _get_proxy(cls) -> Proxy | None:
        """获取代理配置"""
        proxy_url = pconfig.proxy
        if proxy_url:
            return Proxy(url=proxy_url)
        return None

    @handle("pixiv.net", r"pixiv\.net/(?:en/)?artworks/(\d+)")
    async def _parse(self, searched: re.Match[str]) -> ParseResult:
        base_url = self._get_base_url()
        if not base_url:
            from ..exception import IgnoreException

            raise IgnoreException("Pixiv 解析未配置，请设置 parser_pixiv 环境变量")

        illust_id = searched.group(1)
        return await self._fetch_illust(illust_id)

    async def _fetch_illust(self, illust_id: str) -> ParseResult:
        """获取插画详情"""
        from ..exception import ParseException

        base_url = self._get_base_url()
        proxy = self._get_proxy()

        # 获取基本信息（包含 tags 和 xRestrict）
        illust_resp = await self.request(f"{base_url}/ajax/illust/{illust_id}", proxy=proxy)
        illust_data = illust_decoder.decode(illust_resp.content)

        if illust_data.error:
            raise ParseException(f"Pixiv 解析失败: {illust_id}")

        body = illust_data.body

        # 检查 R18/R-18G 限制
        x_restrict = body.get("xRestrict", 0)
        is_restricted = x_restrict >= 1
        if is_restricted and not self._is_r18_allowed():
            from ..exception import IgnoreException

            raise IgnoreException("R18/R-18G 内容已禁用，请开启 parser_pixivR18")

        # 获取图片页面列表
        pages_resp = await self.request(f"{base_url}/ajax/illust/{illust_id}/pages", proxy=proxy)
        pages_data = pages_decoder.decode(pages_resp.content)

        if pages_data.error:
            raise ParseException(f"Pixiv 图片获取失败: {illust_id}")

        # 判断是否为动图
        illust_type = body.get("illustType", 0)
        if illust_type == 2:
            return await self._fetch_ugoira(base_url, illust_id, body)

        return await self._build_result(body, pages_data.body, illust_id, base_url)

    async def _fetch_ugoira(
        self,
        base_url: str,
        illust_id: str,
        body: dict[str, Any],
    ) -> ParseResult:
        """获取 Pixiv 动图 (Ugoira) 详情"""
        from ..exception import ParseException

        proxy = self._get_proxy()

        # 获取动图元数据（ZIP URL 和帧信息）
        ugoira_resp = await self.request(
            f"{base_url}/ajax/illust/{illust_id}/ugoira_meta", proxy=proxy
        )
        ugoira_data = ugoira_decoder.decode(ugoira_resp.content)

        if ugoira_data.error:
            raise ParseException(f"Pixiv 动图元数据获取失败: {illust_id}")

        ugoira_body = ugoira_data.body
        # 优先使用原画 ZIP
        zip_url = ugoira_body.get("originalSrc") or ugoira_body.get("src", "")
        frames: list[dict[str, Any]] = ugoira_body.get("frames", [])

        if not zip_url or not frames:
            raise ParseException(f"Pixiv 动图数据不完整: {illust_id}")

        # 下载 ZIP 文件
        pixiv_headers = {
            **self.headers,
            "Referer": "https://www.pixiv.net/",
        }
        zip_task = self.downloader.download_file(
            zip_url,
            ext_headers=pixiv_headers,
        )

        # 构建简介文本（复用通用逻辑）
        text = self._build_info_text(body, illust_id)
        url = f"https://www.pixiv.net/artworks/{illust_id}"

        return self.result(
            title=body.get("title", ""),
            text=text,
            contents=[
                DynamicContent(
                    path_task=zip_task,
                    gif_path=asyncio.create_task(self._convert_ugoira_to_gif(zip_task, frames)),
                    frames=frames,
                )
            ],
            url=url,
            author=await self._build_author(
                body.get("userId", "") or body.get("user", {}).get("userId", ""),
                body.get("userName", "未知作者"),
                base_url,
            ),
        )

    async def _convert_ugoira_to_gif(
        self,
        zip_task,
        frames: list[dict[str, Any]],
    ):
        """将下载的动图 ZIP 转换为 GIF"""
        from ..utils import convert_ugoira_to_gif

        zip_path = await zip_task
        return await convert_ugoira_to_gif(zip_path, frames)

    async def _build_author(
        self,
        user_id: str,
        user_name: str,
        base_url: str,
    ):
        """构建作者信息"""
        avatar_url = None
        if user_id:
            proxy = self._get_proxy()
            try:
                user_resp = await self.request(
                    f"{base_url}/ajax/user/{user_id}?full=1", proxy=proxy
                )
                user_data = user_decoder.decode(user_resp.content)
                if not user_data.error:
                    avatar_url = user_data.body.get("image")
            except Exception:
                pass

        return self.create_author(
            user_name or "未知作者",
            avatar_url=avatar_url,
            avatar_headers={**self.headers, "Referer": "https://www.pixiv.net/"},
        )

    def _build_info_text(self, body: dict[str, Any], illust_id: str) -> str:
        """构建简介文本"""
        # 标签
        tags_data = body.get("tags", {}).get("tags", []) or []
        tags = [t.get("tag", "") for t in tags_data if t.get("tag") and not t.get("locked")]
        tag_text = " ".join(f"#{t}" for t in tags) if tags else ""

        info_parts: list[str] = []
        if title := body.get("title"):
            info_parts.append(f"标题: {title}")
        if user := body.get("user", {}):
            info_parts.append(f"作者: {user.get('userName', '未知作者')}")
        if upload_date := body.get("uploadDate"):
            info_parts.append(f"发布时间: {upload_date}")
        if tag_text:
            info_parts.append(f"标签: {tag_text}")
        if description := body.get("description"):
            desc_short = description[:200] + "..." if len(description) > 200 else description
            info_parts.append(f"简介: {desc_short}")
        info_parts.append(f"来源: https://www.pixiv.net/artworks/{illust_id}")

        return "\n".join(info_parts)

    async def _build_result(
        self,
        body: dict,
        pages: list[dict],
        illust_id: str,
        base_url: str,
    ) -> ParseResult:
        """构建解析结果（静态插画）"""
        from ..download import DOWNLOADER

        title = body.get("title", "")
        description = body.get("description", "") or ""
        user = body.get("user", {}) or {}
        author_name = body.get("userName", "") or user.get("userName", "未知作者")
        author_id = body.get("userId", "") or user.get("userId", "")
        author_url = f"https://www.pixiv.net/users/{author_id}" if author_id else None
        upload_date = body.get("uploadDate", "")

        # 标签
        tags_data = body.get("tags", {}).get("tags", []) or []
        tags = [t.get("tag", "") for t in tags_data if t.get("tag") and not t.get("locked")]
        tag_text = " ".join(f"#{t}" for t in tags) if tags else ""

        # 图片 URL
        image_urls = [page.get("urls", {}).get("original") for page in pages]
        image_urls = [url for url in image_urls if url]

        # Pixiv 图片需要 Referer 才能下载
        pixiv_headers = {
            **self.headers,
            "Referer": "https://www.pixiv.net/",
        }
        contents: list[ImageContent] = []
        for url in image_urls:
            task = DOWNLOADER.download_img(url, ext_headers=pixiv_headers)
            contents.append(ImageContent(task))

        # 构建简介文本
        info_parts: list[str] = []
        if title:
            info_parts.append(f"标题: {title}")
        info_parts.append(f"作者: {author_name}")
        if author_url:
            info_parts.append(f"Pixiv: {author_url}")
        if upload_date:
            info_parts.append(f"发布时间: {upload_date}")
        if tag_text:
            info_parts.append(f"标签: {tag_text}")
        if description:
            desc_short = description[:200] + "..." if len(description) > 200 else description
            info_parts.append(f"简介: {desc_short}")

        text = "\n".join(info_parts)
        url = f"https://www.pixiv.net/artworks/{illust_id}"

        return self.result(
            title=title,
            text=text,
            contents=contents,
            url=url,
            author=await self._build_author(author_id, author_name, base_url),
        )
