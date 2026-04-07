import re
from typing import Any, ClassVar

from httpx import Proxy, AsyncClient
from msgspec import Struct
from msgspec.json import Decoder

from .base import BaseParser, handle
from .data import Platform, ParseResult, ImageContent
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


pages_decoder = Decoder(PixivPagesResponse)
illust_decoder = Decoder(PixivIllustResponse)


class PixivParser(BaseParser):
    """Pixiv 解析器"""

    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.PIXIV, display_name="Pixiv"
    )

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

        async with AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
            proxy=proxy,
        ) as client:
            # 获取基本信息（包含 tags 和 xRestrict）
            illust_resp = await client.get(f"{base_url}/ajax/illust/{illust_id}")
            illust_resp.raise_for_status()
            illust_data = illust_decoder.decode(illust_resp.content)

            if illust_data.error:
                raise ParseException(f"Pixiv 解析失败: {illust_id}")

            body = illust_data.body

            # 检查 R18/R-18G 限制
            x_restrict = body.get("xRestrict", 0)
            is_restricted = x_restrict >= 1
            if is_restricted and not self._is_r18_allowed():
                from ..exception import IgnoreException

                raise IgnoreException(
                    "R18/R-18G 内容已禁用，请开启 par_pixivR18"
                )

            # 获取图片页面列表
            pages_resp = await client.get(
                f"{base_url}/ajax/illust/{illust_id}/pages"
            )
            pages_resp.raise_for_status()
            pages_data = pages_decoder.decode(pages_resp.content)

            if pages_data.error:
                raise ParseException(f"Pixiv 图片获取失败: {illust_id}")

        return self._build_result(body, pages_data.body, illust_id)

    def _build_result(
        self,
        body: dict,
        pages: list[dict],
        illust_id: str,
    ) -> ParseResult:
        """构建解析结果"""
        from ..download import DOWNLOADER

        # 标题
        title = body.get("title", "")

        # 描述
        description = body.get("description", "") or ""

        # 作者信息
        user = body.get("user", {}) or {}
        author_name = user.get("userName", "未知作者")
        author_id = user.get("userId", "")
        author_url = (
            f"https://www.pixiv.net/users/{author_id}" if author_id else None
        )

        # 标签
        tags_data = body.get("tags", {}).get("tags", []) or []
        tags = [
            t.get("tag", "")
            for t in tags_data
            if t.get("tag") and not t.get("locked")
        ]
        tag_text = " ".join(f"#{t}" for t in tags) if tags else ""

        # 发布时间
        upload_date = body.get("uploadDate", "")

        # 图片 URL
        image_urls = [page.get("urls", {}).get("original") for page in pages]
        image_urls = [url for url in image_urls if url]

        # 创建图片内容（直接发送图片，不渲染预览图）
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
            desc_short = (
                description[:200] + "..."
                if len(description) > 200
                else description
            )
            info_parts.append(f"简介: {desc_short}")

        text = "\n".join(info_parts)
        url = f"https://www.pixiv.net/artworks/{illust_id}"

        return self.result(
            title=title,
            text=text,
            contents=contents,
            url=url,
            author=self.create_author(author_name),
        )
