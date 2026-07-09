import re
from typing import Literal

from msgspec import Struct, DecodeError
from nonebot import logger
from msgspec.json import Decoder
from nonebot.rule import Rule
from nonebot.params import Depends
from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.adapters import Event
from nonebot.plugin.on import get_matcher_source
from nonebot.permission import Permission
from nonebot_plugin_uninfo import Session, UniSession
from nonebot_plugin_alconna.uniseg import Hyper, UniMsg

from .filter import is_enabled
from ..config import gconfig, pconfig

# 统一的状态键
PSR_SEARCHED_KEY: Literal["psr-searched"] = "psr-searched"
PSR_FORCE_PARSE_KEY: Literal["psr-force-parse"] = "psr-force-parse"


# 定义 JSON 卡片的数据结构
class MetaDetail(Struct):
    qqdocurl: str | None = None


class MetaNews(Struct):
    jumpUrl: str | None = None


class MetaMusic(Struct):
    jumpUrl: str | None = None


class Meta(Struct):
    detail_1: MetaDetail | None = None
    news: MetaNews | None = None
    music: MetaMusic | None = None


class RawData(Struct):
    # app 字段标识卡片类型 (QQ 卡片的 com.tencent.xxx), 用于诊断未覆盖的卡片形态
    app: str | None = None
    meta: Meta | None = None


raw_decoder = Decoder(RawData)


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
    """处理 JSON 类型的消息段，提取 URL"""
    data = hyper.data
    raw_str: str | None = data.get("raw")

    if raw_str is None:
        return None

    try:
        raw = raw_decoder.decode(raw_str)
    except DecodeError:
        logger.exception(f"json 卡片解析失败: {raw_str}")
        return None

    # 提取 app 类型(QQ 卡片的 com.tencent.xxx), 便于诊断未知卡片形态
    app = _get_card_app(raw)

    if not raw.meta:
        # 卡片有内容但无 meta 字段 —— 可能是未覆盖的卡片类型, 打印原文供诊断
        logger.warning(f"json 卡片无 meta 字段, 无法提取 URL (app={app}): {raw_str[:300]}")
        return None

    meta, url = raw.meta, None

    if meta.detail_1:
        url = meta.detail_1.qqdocurl
    elif meta.news:
        url = meta.news.jumpUrl
    elif meta.music:
        url = meta.music.jumpUrl

    if url:
        logger.debug(f"extract url[{url}] from raw#meta[{meta}]")
    else:
        # meta 存在但 detail_1/news/music 都不匹配 —— 该卡片类型的 URL 字段位置未知,
        # 打印 app 类型 + meta 结构原文, 供后续扩展 _extract_url 覆盖 (如 com.tencent.qqmusic)
        logger.warning(
            f"json 卡片 meta 无已知 URL 字段 (app={app}, meta keys={meta.__struct_fields__}): {raw_str[:500]}"
        )
    return url


def _get_card_app(raw: RawData) -> str | None:
    """从卡片 JSON 提取 app 字段 (QQ 卡片的 com.tencent.xxx), 用于诊断卡片类型。"""
    return raw.app


def _extract_text(message: UniMsg) -> str | None:
    """从消息中提取文本"""
    if hyper := next(iter(message.get(Hyper, 1)), None):
        return _extract_url(hyper)
    elif plain_text := message.extract_plain_text().strip():
        return plain_text
    return None


def _extract_reply_text(reply: object) -> str:
    """从被引用消息提取文本/URL, 优先解析 JSON 卡片。

    OneBot v11 的 extract_plain_text() 对 json 卡片段返回空 (is_text() 为 False),
    会丢弃卡片里的 jumpUrl/qqdocurl。而 reply 场景下被引用消息常见为分享卡片
    (如 B站/抖音/小红书转发卡), 故需优先扫 json 段, 用 _extract_url 解析出 URL;
    无卡片时回退到 extract_plain_text()。

    reply.message 可能是任意 adapter 的 Message 对象, 这里只依赖最小鸭子类型:
    可迭代出 (type, data_dict) 段 + 有 extract_plain_text。非 OneBot v11 的
    adapter 若无 json 段则自然回退纯文本路径。
    """
    message = getattr(reply, "message", None)
    if message is None:
        return ""
    # 优先扫 json 段提取卡片 URL (extract_plain_text 会跳过这些段)
    for seg in message:
        seg_type = getattr(seg, "type", None)
        if seg_type != "json":
            continue
        data = getattr(seg, "data", None) or {}
        raw = data.get("data") or data.get("raw")
        if raw:
            hyper = Hyper("json", raw=raw)
            if url := _extract_url(hyper):
                return url
    # 无卡片, 回退纯文本
    return message.extract_plain_text().strip()


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

    async def __call__(self, message: UniMsg, event: Event, state: T_State) -> bool:
        text = _extract_text(message)
        if not text:
            return False

        # 检查是否使用了解析前缀强制触发
        parse_prefix = pconfig.parse_prefix

        # 标记是否触发了强制解析 (前缀命中)
        force_parse = False

        # 如果没有设置前缀，跳过前缀检查
        if not parse_prefix:
            state[PSR_FORCE_PARSE_KEY] = False
        else:
            # 检查前缀模式: prefix+ / prefix（空格）/ 纯 prefix（引用回复场景）
            # 纯 prefix (如 "par") 配合引用回复时, 从被引用消息提取 URL
            if text == parse_prefix or text.startswith(f"{parse_prefix}+") or text.startswith(f"{parse_prefix} "):
                force_parse = True
                if text.startswith(f"{parse_prefix}+"):
                    text = text[len(f"{parse_prefix}+") :].lstrip()
                elif text.startswith(f"{parse_prefix} "):
                    text = text[len(f"{parse_prefix} ") :].lstrip()
                else:
                    # 纯前缀: text == parse_prefix, 清空以触发引用回退
                    text = ""
                logger.debug(f"检测到前缀 '{parse_prefix}' 强制解析，去除后: '{text[:50]}'")

        state[PSR_FORCE_PARSE_KEY] = force_parse

        # 引用回复场景: 用户只输入前缀 (无 URL), 从被引用消息提取 URL。
        # 规则在 Matcher 运行 (ensure_context) 之前执行, 此时 current_event 尚未设置,
        # 故必须通过依赖注入拿 event。OneBot v11 adapter 在分发前已用 get_msg 填充
        # event.reply.message (含被引用消息完整内容), 故可直接提取。
        # 被引用消息可能是纯文本含 URL, 也可能是 JSON 卡片 (分享卡), 后者需解析
        # meta.detail_1.qqdocurl / news.jumpUrl / music.jumpUrl (见 _extract_reply_text)。
        if force_parse and not text:
            reply = getattr(event, "reply", None)
            if reply:
                text = _extract_reply_text(reply)
                if text:
                    logger.debug(f"前缀强制解析 + 引用回复, 从被引用消息提取: '{text[:50]}'")

        if not text:
            return False

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
