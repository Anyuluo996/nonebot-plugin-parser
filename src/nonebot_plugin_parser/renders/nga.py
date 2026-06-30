from typing_extensions import override

from nonebot import logger, require

from .base import UniMessage, ParseResult, ImageRenderer

# 优先用 htmlrender（容器/默认渲染栈已安装），回退到 htmlkit
try:
    require("nonebot_plugin_htmlrender")
    from nonebot_plugin_htmlrender import template_to_pic as _template_to_pic

    _BACKEND = "htmlrender"
except Exception:  # pragma: no cover - 依赖可选
    try:
        require("nonebot_plugin_htmlkit")
        from nonebot_plugin_htmlkit import template_to_pic as _template_to_pic

        _BACKEND = "htmlkit"
    except Exception:
        _template_to_pic = None  # type: ignore[assignment]
        _BACKEND = None
        logger.warning("NGA 渲染器: htmlrender 与 htmlkit 均不可用，将无法渲染长图")


class Renderer(ImageRenderer):
    """NGA 帖子流渲染器：主楼 + 前 4 楼回复渲染为一张长图。

    用 nga.html.jinja 模板而非通用 card.html.jinja，因为 NGA 帖子是多楼层结构，
    ParseResult 单作者模型放不下，回复楼层在 result.extra["posts"] 里。
    优先复用已安装的 htmlrender 浏览器栈，避免额外依赖。
    """

    @override
    async def render_image(self, result: ParseResult) -> bytes:
        if _template_to_pic is None:
            raise RuntimeError("NGA 渲染器: 无可用的 template_to_pic 后端 (htmlrender/htmlkit)")

        # 回复楼层图片（extra["posts"].images）与主楼图片一样，需在模板渲染前下完，
        # 否则 template_to_pic 访问 .path_uri 时还是 Task，取不到本地路径。
        await result.ensure_downloads_complete()

        from .resources import DEFAULT_AVATAR_PATH
        default_avatar = DEFAULT_AVATAR_PATH.as_uri()

        if _BACKEND == "htmlrender":
            # htmlrender: 关键字参数，支持 pages（viewport 宽度影响渲染）
            return await _template_to_pic(
                template_path=str(self.templates_dir),
                template_name="nga.html.jinja",
                templates={
                    "result": result,
                    "default_avatar": default_avatar,
                },
                pages={"viewport": {"width": 720, "height": 100}},  # type: ignore[call-arg]
            )
        else:
            # htmlkit: 位置参数 (template_path, template_name, templates=)
            return await _template_to_pic(
                self.templates_dir.as_posix(),
                "nga.html.jinja",
                templates={
                    "result": result,
                    "default_avatar": default_avatar,
                },
            )

    @override
    async def render_messages(self, result: ParseResult):
        """NGA 的文字与图片已全部渲染进长图（主楼 graphics + 回复楼 images），
        不再调用 render_contents 重复发送，避免主楼内容以文字消息二次发出。"""
        image_seg = await self.cache_or_render_image(result)

        msg = UniMessage(image_seg)
        if self.append_url:
            urls = (result.display_url, result.repost_display_url)
            msg += "\n".join(url for url in urls if url)
        yield msg

