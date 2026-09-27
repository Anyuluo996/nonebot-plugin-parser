# 导出所有 Parser 类
# 注意：base 必须最先导入，其他平台解析器都依赖它（ruff isort 会打乱顺序，故禁用排序）
# ruff: noqa: I001
from .base import BaseParser as BaseParser
from .nga import NGAParser as NGAParser
from .buff import BuffParser as BuffParser
from .hupu import HupuParser as HupuParser
from .illu import IlluParser as IlluParser
from .acfun import AcfunParser as AcfunParser
from .kugou import KuGouParser as KuGouParser
from .pixiv import PixivParser as PixivParser
from .tieba import TiebaParser as TiebaParser
from .weibo import WeiBoParser as WeiBoParser
from .zhihu import ZhiHuParser as ZhiHuParser
from .douyin import DouyinParser as DouyinParser
from .heybox import HeyBoxParser as HeyBoxParser
from .lofter import LofterParser as LofterParser
from .coolapk import CoolapkParser as CoolapkParser
from .duitang import DuiTangParser as DuiTangParser
from .netease import NCMParser as NCMParser
from .qsmusic import QSMusicParser as QSMusicParser

# QQ 音乐解析的可选增强引擎缺失时不影响插件加载（api.py 会自动回退内置实现）；
# 这里兜底捕获导入异常（如主依赖 qqmusic-api-python 整个缺失），
# 置哨兵后其余平台照常启动。
try:
    from .qqmusic import QQMusicParser as QQMusicParser

    _QQMUSIC_AVAILABLE = True
except ImportError as _qqmusic_import_err:
    QQMusicParser = None  # type: ignore[assignment,misc]
    _QQMUSIC_AVAILABLE = False
    _QQMUSIC_IMPORT_ERROR = _qqmusic_import_err

from .twitter import TwitterParser as TwitterParser
from .bilibili import BilibiliParser as BilibiliParser
from .kuaishou import KuaiShouParser as KuaiShouParser
from .telegram import TelegramParser as TelegramParser
from .xiaohongshu import XiaoHongShuParser as XiaoHongShuParser
from ..download import YTDLP_DOWNLOADER

# Parser 注册表：从 BaseParser._registry 自动派生（__init_subclass__ 注册时自带
# platform.name）。新增 parser 只要定义 platform 并被 import 到这里即生效，
# 无需再手工登记；可选依赖不可用而未 import 的 parser（如 qqmusic 缺包、
# tiktok/youtube 缺 ytdlp）自然缺席。
PARSERS: dict[str, type[BaseParser]] = {str(cls.platform.name): cls for cls in BaseParser.get_all_subclass()}

if YTDLP_DOWNLOADER is not None:
    from .tiktok import TikTokParser as TikTokParser
    from .youtube import YouTubeParser as YouTubeParser

    PARSERS["tiktok"] = TikTokParser
    PARSERS["youtube"] = YouTubeParser

from .base import handle
from .data import (
    Author,
    Platform,
    ParseResult,
    AudioContent,
    ImageContent,
    VideoContent,
    DynamicContent,
)

__all__ = [
    "PARSERS",
    "AudioContent",
    "Author",
    "BaseParser",
    "DynamicContent",
    "ImageContent",
    "ParseResult",
    "Platform",
    "VideoContent",
    "handle",
]
