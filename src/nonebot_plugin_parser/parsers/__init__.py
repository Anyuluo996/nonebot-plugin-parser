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

# QQ 音乐解析依赖 qqmusic-api-python（主依赖，但允许缺包降级：缺时仅 QQ 音乐解析
# 不可用，其余平台照常启动）。导入失败由 parsers/qqmusic/api.py 顶层对
# qqmusic_api 的 import 触发，这里捕获后置哨兵，不阻塞整个插件加载。
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

# Parser 注册表
PARSERS: dict[str, type[BaseParser]] = {
    "nga": NGAParser,
    "acfun": AcfunParser,
    "weibo": WeiBoParser,
    "douyin": DouyinParser,
    "twitter": TwitterParser,
    "bilibili": BilibiliParser,
    "kuaishou": KuaiShouParser,
    "telegram": TelegramParser,
    "xiaohongshu": XiaoHongShuParser,
    "zhihu": ZhiHuParser,
    "netease": NCMParser,
    "kugou": KuGouParser,
    "qsmusic": QSMusicParser,
    "hupu": HupuParser,
    "coolapk": CoolapkParser,
    "lofter": LofterParser,
    "duitang": DuiTangParser,
    "buff": BuffParser,
    "heybox": HeyBoxParser,
    "illu": IlluParser,
    "tieba": TiebaParser,
    "pixiv": PixivParser,
}

if _QQMUSIC_AVAILABLE:
    PARSERS["qqmusic"] = QQMusicParser  # type: ignore[assignment]

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
