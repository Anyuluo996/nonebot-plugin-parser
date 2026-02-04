import re
from typing import Literal

import msgspec
from nonebot import logger
from nonebot.rule import Rule
from nonebot.params import Depends
from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.plugin.on import get_matcher_source
from nonebot.permission import Permission
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import Hyper, UniMsg

from .filter import is_enabled, is_platform_enabled
from ..config import gconfig, pconfig

# 统一的状态键
PSR_SEARCHED_KEY: Literal["psr-searched"] = "psr-searched"
PSR_FORCE_PARSE_KEY: Literal["psr-force-parse"] = "psr-force-parse"


# 定义 JSON 卡片的数据结构
class MetaDetail(msgspec.Struct):
    qqdocurl: str | None = None


class MetaNews(msgspec.Struct):
    jumpUrl: str | None = None


class MetaMusic(msgspec.Struct):
    jumpUrl: str | None = None


class Meta(msgspec.Struct):
    detail_1: MetaDetail | None = None
    news: MetaNews | None = None
    music: MetaMusic | None = None


class RawData(msgspec.Struct):
    meta: Meta | None = None


raw_decoder = msgspec.json.Decoder(RawData)


class SearchResult:
    """匹配结果"""

    __slots__ = ("keyword", "searched", "text")

    def __init__(
        self,
        text: str,
        keyword: str,
        searched: re.Match[str],
    ):
        self.text: str = text
        self.keyword: str = keyword
        self.searched: re.Match[str] = searched


def Searched() -> SearchResult:
    """依赖注入，返回 SearchResult"""
    return Depends(_searched)


def _searched(state: T_State) -> SearchResult | None:
    """从 state 中提取匹配结果"""
    return state.get(PSR_SEARCHED_KEY)


def _extract_url(hyper: Hyper) -> str | None:
    """处理 JSON 类型的消息段，提取 URL

    Args:
        json_seg: JSON 类型的消息段

    Returns:
        Optional[str]: 提取的 URL, 如果提取失败则返回 None
    """
    data = hyper.data
    raw_str: str | None = data.get("raw")

    if raw_str is None:
        return None

    try:
        raw = raw_decoder.decode(raw_str)
    except msgspec.DecodeError:
        logger.exception(f"json 卡片解析失败: {raw_str}")
        return None

    if not raw.meta:
        return None

    meta, url = raw.meta, None

    if meta.detail_1:
        url = meta.detail_1.qqdocurl
    elif meta.news:
        url = meta.news.jumpUrl
    elif meta.music:
        url = meta.music.jumpUrl

    logger.debug(f"extract url[{url}] from raw#meta[{meta}]")
    return url


def _extract_text(message: UniMsg) -> str | None:
    """从消息中提取文本"""
    if hyper := next(iter(message.get(Hyper, 1)), None):
        return _extract_url(hyper)
    elif plain_text := message.extract_plain_text().strip():
        return plain_text
    return None


class KeyPatternList(list[tuple[str, re.Pattern[str]]]):
    def __init__(self, *args: tuple[str, str | re.Pattern[str]]):
        super().__init__()
        for key, pattern in args:
            if isinstance(pattern, str):
                pattern = re.compile(pattern)
            self.append((key, pattern))
        # 按 key 长 -> 短
        self.sort(key=lambda x: -len(x[0]))
        logger.debug(f"KeyWords: {[k for k, _ in self]}")


class KeywordRegexRule:
    """检查消息是否含有关键词, 有关键词进行正则匹配"""

    __slots__ = ("key_pattern_list",)

    def __init__(self, key_pattern_list: KeyPatternList):
        self.key_pattern_list = key_pattern_list

    def __repr__(self) -> str:
        return f"KeywordRegex(key_pattern_list={self.key_pattern_list})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, KeywordRegexRule) and self.key_pattern_list == other.key_pattern_list

    def __hash__(self) -> int:
        return hash(frozenset(self.key_pattern_list))

    async def __call__(self, message: UniMsg, state: T_State) -> bool:
        text = _extract_text(message)
        if not text:
            return False

        # 检查是否使用了解析前缀强制触发
        force_parse = False
        parse_prefix = pconfig.parse_prefix

        # 如果没有设置前缀，跳过前缀检查
        if not parse_prefix:
            state[PSR_FORCE_PARSE_KEY] = False
        else:
            # 检查前缀模式: prefix+ 或 prefix（空格）
            if text.startswith(f"{parse_prefix}+") or text.startswith(f"{parse_prefix} "):
                force_parse = True
                # 去除前缀
                if text.startswith(f"{parse_prefix}+"):
                    text = text[len(f"{parse_prefix}+"):].lstrip()
                else:
                    text = text[len(f"{parse_prefix} "):].lstrip()
                logger.debug(f"检测到前缀 '{parse_prefix}' 强制解析，去除后: '{text[:50]}...'")
                state[PSR_FORCE_PARSE_KEY] = True
            else:
                state[PSR_FORCE_PARSE_KEY] = False

        for keyword, pattern in self.key_pattern_list:
            if keyword not in text:
                continue
            if searched := pattern.search(text):
                state[PSR_SEARCHED_KEY] = SearchResult(text=text, keyword=keyword, searched=searched)
                return True
            logger.debug(f"keyword '{keyword}' is in '{text}', but not matched")
        return False


def keyword_regex(*args: tuple[str, str | re.Pattern[str]]) -> Rule:
    return Rule(KeywordRegexRule(KeyPatternList(*args)))


def on_keyword_regex(*args: tuple[str, str | re.Pattern[str]], priority: int = 5) -> type[Matcher]:
    matcher = Matcher.new(
        "message",
        is_enabled & keyword_regex(*args),
        priority=priority,
        block=True,
        source=get_matcher_source(1),
    )
    return matcher


async def _is_super_private(sess: Session | None = UniSession()) -> bool:
    if not sess:
        return False
    return sess.scene.is_private and sess.user.id in gconfig.superusers


SUPER_PRIVATE = Permission(_is_super_private)
