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
        logger.warning("知乎渲染器: htmlrender 与 htmlkit 均不可用，将无法渲染长图")


class Renderer(ImageRenderer):
    """知乎专用渲染器。

    仅问题页（有多回答，result.extra["answers"] 非空）走 zhihu.html.jinja 长图模板；
    专栏文章 / 单条回答页无 extra["answers"]，委托全局渲染器（card 模板，支持视频/封面）。
    """

    def _is_multi_answer(self, result: ParseResult) -> bool:
        return bool(result.extra.get("answers"))

    @override
    async def render_image(self, result: ParseResult) -> bytes:
        # 非问题页（文章/单条回答）：委托全局渲染器
        if not self._is_multi_answer(result):
            from . import get_global_renderer

            global_renderer = get_global_renderer()
            return await global_renderer.render_image(result)  # type: ignore[attr-defined]

        if _template_to_pic is None:
            raise RuntimeError("知乎渲染器: 无可用的 template_to_pic 后端 (htmlrender/htmlkit)")

        # 回答里的图片（extra["answers"].content 中的 ImageContent）需在模板渲染前下完，
        # 否则 template_to_pic 访问 .path_uri 时还是 Task，取不到本地路径。
        await result.ensure_downloads_complete()

        from .resources import DEFAULT_AVATAR_PATH
        default_avatar = DEFAULT_AVATAR_PATH.as_uri()

        if _BACKEND == "htmlrender":
            return await _template_to_pic(
                template_path=str(self.templates_dir),
                template_name="zhihu.html.jinja",
                templates={
                    "result": result,
                    "default_avatar": default_avatar,
                },
                pages={"viewport": {"width": 720, "height": 100}},  # type: ignore[call-arg]
            )
        else:
            return await _template_to_pic(
                self.templates_dir.as_posix(),
                "zhihu.html.jinja",
                templates={
                    "result": result,
                    "default_avatar": default_avatar,
                },
            )

    @override
    async def render_messages(self, result: ParseResult):
        # 非问题页：委托全局渲染器
        if not self._is_multi_answer(result):
            from . import get_global_renderer

            global_renderer = get_global_renderer()
            async for msg in global_renderer.render_messages(result):
                yield msg
            return

        """知乎问题页的文字与图片已全部渲染进长图（主楼 graphics + 回答 content），
        不再调用 render_contents 重复发送，避免主楼内容以文字消息二次发出。"""
        image_seg = await self.cache_or_render_image(result)

        msg = UniMessage(image_seg)
        if self.append_url:
            urls = (result.display_url, result.repost_display_url)
            msg += "\n".join(url for url in urls if url)
        yield msg
