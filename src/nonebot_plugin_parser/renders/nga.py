from typing_extensions import override

from nonebot import require

require("nonebot_plugin_htmlkit")
from nonebot_plugin_htmlkit import template_to_pic

from .base import ParseResult, ImageRenderer


class Renderer(ImageRenderer):
    """NGA 帖子流渲染器：主楼 + 前 4 楼回复渲染为一张长图。"""

    @override
    async def render_image(self, result: ParseResult) -> bytes:
        # 回复楼层图片（extra["posts"].images）与主楼图片一样，需在模板渲染前下完，
        # 否则 template_to_pic 访问 .path_uri 时还是 Task，取不到本地路径。
        await result.ensure_downloads_complete()
        from .resources import DEFAULT_AVATAR_PATH

        return await template_to_pic(
            self.templates_dir.as_posix(),
            "nga.html.jinja",
            templates={
                "result": result,
                "default_avatar": DEFAULT_AVATAR_PATH.as_uri(),
            },
        )

