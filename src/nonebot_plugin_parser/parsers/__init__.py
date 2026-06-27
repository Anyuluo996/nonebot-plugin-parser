# 导出所有 Parser 类
from .nga import NGAParser as NGAParser
from .base import BaseParser as BaseParser
from .buff import BuffParser as BuffParser
from .hupu import HupuParser as HupuParser
from .kuwo import KuWoParser as KuWoParser
from .acfun import AcfunParser as AcfunParser
from .kugou import KuGouParser as KuGouParser
from .pixiv import PixivParser as PixivParser
from .weibo import WeiBoParser as WeiBoParser
from .zhihu import ZhiHuParser as ZhiHuParser
from .douyin import DouyinParser as DouyinParser
from .lofter import LofterParser as LofterParser
from .coolapk import CoolapkParser as CoolapkParser
from .duitang import DuiTangParser as DuiTangParser
from .netease import NCMParser as NCMParser
from .qsmusic import QSMusicParser as QSMusicParser
from .twitter import TwitterParser as TwitterParser
from .bilibili import BilibiliParser as BilibiliParser
from .kuaishou import KuaiShouParser as KuaiShouParser
from .telegram import TelegramParser as TelegramParser
from ..download import YTDLP_DOWNLOADER
from .xiaohongshu import XiaoHongShuParser as XiaoHongShuParser

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
    "kuwo": KuWoParser,
    "qsmusic": QSMusicParser,
    "hupu": HupuParser,
    "coolapk": CoolapkParser,
    "lofter": LofterParser,
    "duitang": DuiTangParser,
    "buff": BuffParser,
    "pixiv": PixivParser,
}

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
