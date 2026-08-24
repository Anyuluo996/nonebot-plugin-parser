from typing import Any
from typing_extensions import override

from nonebot import logger, require

from .base import MAX_LONG_IMAGE_HEIGHT, UniHelper, UniMessage, ParseResult, ImageRenderer

# 优先用 htmlrender（容器/默认渲染栈已安装），回退到 htmlkit
_template_to_pic = None  # htmlkit 后端符号, 仅 _BACKEND == "htmlkit" 时被赋值
try:
    require("nonebot_plugin_htmlrender")
    _BACKEND = "htmlrender"
except Exception:  # pragma: no cover - 依赖可选
    try:
        require("nonebot_plugin_htmlkit")
        from nonebot_plugin_htmlkit import template_to_pic as _template_to_pic

        _BACKEND = "htmlkit"
    except Exception:
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

        if _BACKEND is None:
            raise RuntimeError("知乎渲染器: 无可用的渲染后端 (htmlrender/htmlkit)")

        # 回答里的图片（extra["answers"].content 中的 ImageContent）需在模板渲染前下完，
        # 否则渲染时访问 .path_uri 时还是 Task，取不到本地路径。
        # 仅等图片资源(img_only), 不等视频 — 视频由 render_contents 独立发送。
        await result.ensure_downloads_complete(img_only=True)

        from .resources import DEFAULT_AVATAR_PATH
        from ..parsers.data import path_to_data_uri

        default_avatar = path_to_data_uri(DEFAULT_AVATAR_PATH)
        variables = {
            "result": result,
            "default_avatar": default_avatar,
        }

        if _BACKEND == "htmlrender":
            from nonebot_plugin_htmlrender import render_template

            artifact = await render_template(
                template_path=str(self.templates_dir),
                template_name="zhihu.html.jinja",
                variables=variables,
                width=720,
            )
            return bytes(artifact)
        else:
            if _template_to_pic is None:  # pragma: no cover - _BACKEND 守卫已排除
                raise RuntimeError("知乎渲染器: htmlkit 后端不可用")
            return await _template_to_pic(
                self.templates_dir.as_posix(),
                "zhihu.html.jinja",
                templates=variables,
            )

    @override
    async def render_messages(self, result: ParseResult):
        # 非问题页：委托全局渲染器（其 render_messages 自带切片逻辑）
        if not self._is_multi_answer(result):
            from . import get_global_renderer

            global_renderer = get_global_renderer()
            async for msg in global_renderer.render_messages(result):
                yield msg
            return

        # 知乎问题页的文字与图片已全部渲染进长图（主楼 graphics + 回答 content），
        # 不再调用 render_contents 重复发送，避免主楼内容以文字消息二次发出。
        # 长图切片逻辑（MAX_LONG_IMAGE_HEIGHT）复用基类 ImageRenderer._split_long_image。
        image_seg, image_raw = await self.cache_or_render_image(result)

        slices = await self._split_long_image(image_raw)
        if len(slices) > 1:
            logger.debug(f"知乎长图切片: {len(slices)} 张 (每张 ≤{MAX_LONG_IMAGE_HEIGHT}px)")

        url_text = ""
        if self.append_url:
            urls = (result.display_url, result.repost_display_url)
            url_text = "\n".join(url for url in urls if url)

        if len(slices) == 1:
            # 单图：直接发送（URL 追加为文本）
            msg: UniMessage[Any] = UniMessage(image_seg)
            if url_text:
                msg += url_text
            yield msg
        else:
            # 多图：合并转发，URL 作为末尾文本节点
            segs = [UniHelper.img_seg(piece) for piece in slices]
            nodes: list[Any] = list(segs)
            if url_text:
                nodes.append(url_text)
            yield UniMessage(UniHelper.construct_forward_message(nodes))
