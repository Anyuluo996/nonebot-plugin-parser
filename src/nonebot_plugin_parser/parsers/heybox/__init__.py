"""小黑盒解析器。

> ⚠️ 实验性：签名算法已实现，但存在 IP 级风控（show_captcha），
> 同一出口 IP 频繁请求会触发验证码，需配合代理或更换出口 IP。

适配自 parser-lite 的 heybox，参考 zhiyu1998/rconsole-plugin 的 nonce 算法。
反爬策略：优先 token + httpx（快）；失败则浏览器页面内 fetch（原生指纹，慢但可靠）。
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
        from nonebot_plugin_htmlrender import get_default_application
    except ImportError:
        return None
    try:
        # htmlrender 0.8: page() yield 的就是 playwright Page
        app = get_default_application()
        async with app.extensions.playwright.page(viewport={"width": 1280, "height": 800}) as page:
            await page.goto("https://www.xiaoheihe.cn/", wait_until="networkidle", timeout=20_000)
            await page.wait_for_timeout(1500)
            token = await page.evaluate("window.SMSdk && window.SMSdk.getDeviceId ? window.SMSdk.getDeviceId() : null")
        return token
    except Exception as e:
        logger.warning(f"小黑盒 tokenid 获取失败: {e!r}")
        return None


async def _browser_fetch_link(link_id: str) -> dict | None:
    """用浏览器打开小黑盒首页后，在页面上下文内 fetch API。

    浏览器原生 TLS 指纹/JS/cookie 全带上，绕过风控最可靠（作为兜底）。
    """
    try:
        from nonebot_plugin_htmlrender import get_default_application
    except ImportError:
        return None
    try:
        url = build_url(link_id)
        # 同 _fetch_token_id，htmlrender 0.8 的 page() 直接返回 playwright Page
        app = get_default_application()
        async with app.extensions.playwright.page(viewport={"width": 1280, "height": 800}) as page:
            await page.goto("https://www.xiaoheihe.cn/", wait_until="networkidle", timeout=20_000)
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
    except Exception as e:
        logger.warning(f"小黑盒浏览器请求失败: {e!r}")
        return None


class HeyBoxParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.HEYBOX, display_name="小黑盒")

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

    async def _httpx_request(self, link_id: str) -> dict:
        cookies = {"x_xhh_tokenid": HeyBoxParser._token_id} if HeyBoxParser._token_id else None
        response = await self.request(build_url(link_id), headers=self.headers, cookies=cookies)
        response.raise_for_status()
        return response.json()

    @handle("xiaoheihe.cn/app/bbs", r"link\/(?P<link_id>[A-Za-z0-9]+)")
    @handle("xiaoheihe.cn/bbs/post_share", r"link_id=(?P<link_id>[A-Za-z0-9]+)")
    @handle("xiaoheihe.cn/v3/bbs/app/api/web/share", r"link_id=(?P<link_id>[A-Za-z0-9]+)")
    async def _parse(self, searched: re.Match[str]):
        link_id = searched.group("link_id")

        # 首次获取 token（反爬必需）
        if not HeyBoxParser._token_id:
            HeyBoxParser._token_id = await _fetch_token_id()

        # 路径1：token + httpx（快）
        res = await self._httpx_request(link_id)

        # 路径2：失败则浏览器页面内 fetch（慢，原生指纹兜底）
        if res.get("status") != "ok":
            logger.debug("小黑盒 httpx 失败，尝试浏览器 fetch 兜底")
            HeyBoxParser._token_id = None  # token 可能失效，清空
            res = await _browser_fetch_link(link_id) or {}

        if res.get("status") != "ok":
            raise ParseException(f"小黑盒解析失败: {res}")

        data = convert(res["result"], BaseResult)
        link = data.link

        graphics = link.to_graphics(self.create_image_content, self.create_video_content)

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
