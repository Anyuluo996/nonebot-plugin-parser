"""小黑盒解析器。

适配自 parser-lite 的 heybox。使用 hkey 签名 API（encrypt.py）。
小黑盒要求 x_xhh_tokenid cookie（反爬），通过浏览器执行
window.SMSdk.getDeviceId() 获取（复用 nonebot_plugin_htmlrender）。
无浏览器时降级为匿名请求，部分帖子会触发 show_captcha。
"""

import re
from typing import ClassVar

from msgspec import convert
from nonebot import logger

from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from .model import BaseResult
from .encrypt import build_url
from .._format import format_num


async def _fetch_token_id() -> str | None:
    """通过浏览器打开小黑盒首页，执行 JS 获取 x_xhh_tokenid。"""
    try:
        from nonebot_plugin_htmlrender import get_new_page
    except ImportError:
        return None
    try:
        async with get_new_page(viewport={"width": 1280, "height": 800}) as page:
            await page.goto(
                "https://www.xiaoheihe.cn/", wait_until="networkidle", timeout=20_000
            )
            await page.wait_for_timeout(1500)
            token = await page.evaluate(
                "window.SMSdk && window.SMSdk.getDeviceId"
                " ? window.SMSdk.getDeviceId() : null"
            )
        if token:
            logger.debug(f"小黑盒 tokenid 获取成功: {str(token)[:8]}...")
        return token
    except Exception as e:
        logger.warning(f"小黑盒 tokenid 获取失败: {e!r}")
        return None


class HeyBoxParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.HEYBOX, display_name="小黑盒"
    )

    _token_id: ClassVar[str | None] = None

    def __init__(self):
        super().__init__()
        self.headers.update(
            {
                "Referer": "https://www.xiaoheihe.cn/",
                "Origin": "https://www.xiaoheihe.cn",
                "Accept": "application/json, text/plain, */*",
            }
        )

    async def _request_link(self, link_id: str) -> dict:
        """请求小黑盒 API，带 tokenid cookie（反爬必需）。"""
        cookies = (
            {"x_xhh_tokenid": HeyBoxParser._token_id}
            if HeyBoxParser._token_id
            else None
        )
        response = await self.request(
            build_url(link_id), headers=self.headers, cookies=cookies
        )
        response.raise_for_status()
        return response.json()

    @handle("xiaoheihe.cn/app/bbs", r"link\/(?P<link_id>[A-Za-z0-9]+)")
    @handle("xiaoheihe.cn/bbs/post_share", r"link_id=(?P<link_id>[A-Za-z0-9]+)")
    async def _parse(self, searched: re.Match[str]):
        link_id = searched.group("link_id")

        # 首次或失效时通过浏览器获取 tokenid（反爬必需）
        if not HeyBoxParser._token_id:
            HeyBoxParser._token_id = await _fetch_token_id()

        res = await self._request_link(link_id)

        # token 失效时（show_captcha/failed）清空并重试一次
        if res.get("status") != "ok":
            logger.debug("小黑盒 token 可能失效，重新获取")
            HeyBoxParser._token_id = await _fetch_token_id()
            if HeyBoxParser._token_id:
                res = await self._request_link(link_id)

        if res.get("status") != "ok":
            raise ParseException(f"小黑盒解析失败: {res}")

        data = convert(res["result"], BaseResult)
        link = data.link

        graphics = link.to_graphics(
            self.create_image_content, self.create_video_content
        )

        return self.result(
            title=link.title,
            graphics=graphics,
            timestamp=link.create_at,
            url=f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}",
            author=self.create_author(
                name=link.user.username,
                avatar_url=link.user.avatar_url,
            ),
            extra={
                "info": (
                    f"浏览 {format_num(link.click)} | "
                    f"赞 {format_num(link.link_award_num)} | "
                    f"藏 {format_num(link.favour_count)} | "
                    f"评 {format_num(link.comment_num)}"
                ),
            },
        )
