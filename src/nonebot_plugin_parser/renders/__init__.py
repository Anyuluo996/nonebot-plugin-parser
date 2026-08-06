import importlib

from nonebot import get_driver

from .. import utils
from .base import BaseRenderer
from .common import CommonRenderer
from .default import DefaultRenderer

_HTML_RENDER_AVAILABLE = utils.is_module_available("nonebot_plugin_htmlrender")
_HTMLKIT_AVAILABLE = utils.is_module_available("nonebot_plugin_htmlkit")

_COMMON_RENDERER = CommonRenderer()
_DEFAULT_RENDERER = DefaultRenderer()
RENDERER = None

# 平台 → 渲染器实例缓存。单线程 asyncio 下 get_renderer 同步执行无 await,
# 不存在并发重入, 无需加锁。避免每次解析都 importlib.import_module + 实例化。
_RENDERER_CACHE: dict[str, BaseRenderer] = {}

from ..config import pconfig
from ..constants import RenderType

match pconfig.render_type:
    case RenderType.common:
        RENDERER = _COMMON_RENDERER
    case RenderType.default:
        RENDERER = _DEFAULT_RENDERER
    case RenderType.htmlrender if _HTML_RENDER_AVAILABLE:
        from .htmlrender import HtmlRenderer

        RENDERER = HtmlRenderer()


def get_renderer(platform: str) -> BaseRenderer:
    """根据平台名称获取对应的 Renderer 类。

    平台覆盖：若 renders/<platform>.py 存在且能导入，优先用平台专用渲染器，
    不受 render_type 影响（例如 NGA 的多楼层模板需要专用渲染器，即便全局是
    htmlrender/common 模式也要走它）。导入失败（如缺依赖、无专用渲染器）时
    回退至全局 RENDERER，避免影响其他平台。

    实例按 platform 缓存（``_RENDERER_CACHE``）：渲染器类均无实例级可变状态
    （per-render 工作状态在 RenderContext / 方法局部变量 / 入参 result 上），
    故跨请求复用同一实例安全; 类资源（字体/logo）由 ``load_resources`` 在启动时
    一次性加载到类属性, 实例化本身很轻。
    """
    cached = _RENDERER_CACHE.get(platform)
    if cached is not None:
        return cached

    try:
        module = importlib.import_module("." + platform, package=__name__)
        renderer_class: type[BaseRenderer] = getattr(module, "Renderer")
        renderer = renderer_class()
    except ModuleNotFoundError:
        # 该平台无 renders/<platform>.py（多数平台走这里，正常路径）
        renderer = get_global_renderer()
    except Exception as e:
        # 有专用渲染器但导入失败（如依赖 htmlkit 未安装），回退全局，避免影响该平台
        from nonebot import logger

        logger.debug(f"平台 {platform} 专用渲染器加载失败，回退全局渲染器: {e!r}")
        renderer = get_global_renderer()

    _RENDERER_CACHE[platform] = renderer
    return renderer


def get_global_renderer() -> BaseRenderer:
    """获取全局渲染器（受 render_type 配置控制）。

    供平台专用渲染器在「某些结果不需要专用模板」时回退使用，例如知乎的文章/单条回答
    走通用 card 模板，只有问题页（多回答）走专用 zhihu 模板。
    """
    if RENDERER:
        return RENDERER

    if not _HTMLKIT_AVAILABLE:
        return _COMMON_RENDERER
    return _DEFAULT_RENDERER


@get_driver().on_startup
async def load_resources():
    CommonRenderer.load_resources()
