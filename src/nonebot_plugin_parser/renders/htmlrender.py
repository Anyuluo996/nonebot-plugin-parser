from typing_extensions import override

from nonebot import require

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import template_to_pic

from ..browser_retry import with_browser_retry
from . import resources
from .base import ParseResult, ImageRenderer, pconfig


class HtmlRenderer(ImageRenderer):
    """HTML 渲染器"""

    @override
    async def render_image(self, result: ParseResult) -> bytes:
        await result.ensure_downloads_complete()

        logo = resources.RESOURCES_DIR / f"{result.platform.name}.png"
        logo = logo.as_uri() if logo.exists() else None
        font = pconfig.custom_font or resources.DEFAULT_FONT_PATH
        font = font.as_uri() if font.exists() else None
        play_button = resources.DEFAULT_VIDEO_BUTTON_PATH.as_uri()

        # 包裹 with_browser_retry：浏览器子进程崩溃导致 transport 关闭时，
        # 强制重启全局浏览器实例并重试一次，避免单次解析失败。
        return await with_browser_retry(
            lambda: template_to_pic(
                template_path=str(self.templates_dir),
                template_name="card.html.jinja",
                templates={
                    "result": result,
                    "logo": logo,
                    "font": font,
                    "play_button": play_button,
                },
                pages={"viewport": {"width": 800, "height": 100}},
            )
        )
