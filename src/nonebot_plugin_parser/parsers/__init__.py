# 导出所有 Parser 类
from .nga import NGAParser as NGAParser
from .base import BaseParser as BaseParser
from .acfun import AcfunParser as AcfunParser
from .pixiv import PixivParser as PixivParser
from .weibo import WeiBoParser as WeiBoParser
from .douyin import DouyinParser as DouyinParser
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
