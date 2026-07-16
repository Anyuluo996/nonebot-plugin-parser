import io
from typing import Any
from typing_extensions import override

from nonebot import logger, require

from .base import UniHelper, UniMessage, ParseResult, ImageRenderer

# QQ NT 内核对单张图片高度有上限（约 4000-5000px），超长会 rich media transfer failed。
# NGA 长图内容无限增长，渲染后按此阈值垂直切片，逐张发送。
MAX_IMAGE_HEIGHT = 4000

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
        不再调用 render_contents 重复发送，避免主楼内容以文字消息二次发出。

        长图超过 QQ NT 单图高度上限时，垂直切片逐张发送，避免 rich media transfer failed。
        """
        image_raw = await self.render_image(result)
        slices = await self._split_long_image(image_raw)
        if len(slices) > 1:
            logger.debug(f"NGA 长图切片: {len(slices)} 张 (每张 ≤{MAX_IMAGE_HEIGHT}px)")

        urls = (result.display_url, result.repost_display_url) if self.append_url else ()
        url_text = "\n".join(url for url in urls if url)

        for idx, piece in enumerate(slices):
            msg: UniMessage[Any] = UniMessage(UniHelper.img_seg(piece))
            # URL 只追加到最后一张，避免每片重复
            if idx == len(slices) - 1 and url_text:
                msg += url_text
            yield msg

    @staticmethod
    async def _split_long_image(raw: bytes) -> list[bytes]:
        """长图按 MAX_IMAGE_HEIGHT 垂直切片，返回各片 bytes；不超高时原样返回单元素列表。

        Pillow 的 open/crop/save 是同步 CPU 操作，用 to_thread 避免阻塞事件循环。
        """
        import asyncio

        from PIL import Image

        def _do_split() -> list[bytes]:
            img = Image.open(io.BytesIO(raw))
            width, height = img.size
            if height <= MAX_IMAGE_HEIGHT:
                return [raw]
            pieces: list[bytes] = []
            for top in range(0, height, MAX_IMAGE_HEIGHT):
                bottom = min(top + MAX_IMAGE_HEIGHT, height)
                piece = img.crop((0, top, width, bottom))
                buf = io.BytesIO()
                piece.save(buf, format="PNG")
                pieces.append(buf.getvalue())
            return pieces

        return await asyncio.to_thread(_do_split)
