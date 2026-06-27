# 导出所有 Parser 类
# 注意：base 必须最先导入，其他平台解析器都依赖它（ruff isort 会打乱顺序，故禁用排序）
# ruff: noqa: I001
from .base import BaseParser as BaseParser
from .nga import NGAParser as NGAParser
from .buff import BuffParser as BuffParser
from .hupu import HupuParser as HupuParser
from .illu import IlluParser as IlluParser
from .kuwo import KuWoParser as KuWoParser
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
from .qqmusic import QQMusicParser as QQMusicParser
from .baidu_music import BaiduMusicParser as BaiduMusicParser
from .meting_base import MetingBaseParser as MetingBaseParser
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
    "kuwo": KuWoParser,
    "qsmusic": QSMusicParser,
    "qqmusic": QQMusicParser,
    "baidu": BaiduMusicParser,
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
