"""小黑盒解析器。

适配自 parser-lite 的 heybox。使用 hkey 签名 API（encrypt.py）。
小黑盒有严格反爬（show_captcha），需要浏览器原生指纹。
优先用浏览器页面内 fetch（cookie/指纹/TLS 全原生，最可靠）；
无浏览器时降级为 httpx（可能被风控）。
"""

import re
from typing import ClassVar

from msgspec import convert
from nonebot import logger

from .._format import format_num
from ..base import BaseParser, ParseException, Platform, PlatformEnum, handle
from .encrypt import build_url
from .model import BaseResult


async def _browser_fetch_link(link_id: str) -> dict | None:
    """用浏览器打开小黑盒首页后，在页面上下文内 fetch API。

    浏览器原生的 TLS 指纹、JS 环境、cookie 全部带上，最接近真实用户，
    是绕过 show_captcha 风控最可靠的方式。
    """
    try:
        from nonebot_plugin_htmlrender import get_new_page
    except ImportError:
        return None
    try:
        url = build_url(link_id)
        async with get_new_page(viewport={"width": 1280, "height": 800}) as page:
            await page.goto(
                "https://www.xiaoheihe.cn/", wait_until="networkidle", timeout=20_000
            )
            await page.wait_for_timeout(1500)
            data = await page.evaluate(
                """async (url) => {
                    try {
                        const r = await fetch(url, {credentials: "include"});
                        return await r.json();
                    } catch(e) { return null; }
                }""",
                url,
            )
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning(f"小黑盒浏览器请求失败: {e!r}")
        return None


class HeyBoxParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.HEYBOX, display_name="小黑盒"
    )

    def __init__(self):
        super().__init__()
        self.headers.update(
            {
                "Referer": "https://www.xiaoheihe.cn/",
                "Origin": "https://www.xiaoheihe.cn",
                "Accept": "application/json, text/plain, */*",
            }
        )

    @handle("xiaoheihe.cn/app/bbs", r"link\/(?P<link_id>[A-Za-z0-9]+)")
    @handle("xiaoheihe.cn/bbs/post_share", r"link_id=(?P<link_id>[A-Za-z0-9]+)")
    async def _parse(self, searched: re.Match[str]):
        link_id = searched.group("link_id")

        # 优先：浏览器页面内 fetch（原生指纹，绕过风控最可靠）
        res = await _browser_fetch_link(link_id)

        # 降级：httpx 请求（无浏览器或浏览器失败时）
        if not res or res.get("status") != "ok":
            logger.debug("小黑盒浏览器请求未成功，降级 httpx")
            response = await self.request(build_url(link_id), headers=self.headers)
            response.raise_for_status()
            res = response.json()

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
