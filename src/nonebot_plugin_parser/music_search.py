"""音乐点歌搜索聚合层（网易云 / QQ 音乐 / 酷狗）。

对外只暴露 :func:`aggregate_search` 和 :class:`SearchItem`,三个服务的搜索细节
封装在各自的 ``search_*`` 函数里,任意单服务失败 **静默返回空列表**（内部
``logger.debug`` 记录）,不向用户暴露任何服务级错误诊断。

指定服务搜索时（如 ``par网易云``）由上层调用者负责把空结果转成用户提示;
默认三服务并发（``par点歌``）由本模块并发执行,单点失败不影响其他服务补齐。

字段映射（已实测各接口响应结构）:

| 服务   | 接口                              | id 字段 | 歌名字段     | 歌手字段            | 付费字段          |
| ------ | --------------------------------- | ------- | ------------ | ------------------- | ----------------- |
| 网易云 | ``/api/search/get/web``           | ``id``  | ``name``     | ``artists[].name``  | ``fee`` (1=VIP)   |
| QQ音乐 | ``qqmusic_api.general_search``    | ``mid`` | ``name``     | ``singer[].name``   | ``pay.pay_play``  |
| 酷狗   | ``/api/v3/search/song``           | ``hash``| ``songname`` | ``singername``      | ``pay_type`` (3)  |

VIP 过滤：未登录时搜索结果自动剔除付费歌曲（fee/pay_play/pay_type 判定），
并多搜补齐至 ``per_service_limit`` 条免费曲；已登录（凭证可用）则保留 VIP，
后续解析阶段用登录态获取真实播放地址。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal
from dataclasses import dataclass
from collections.abc import Callable, Awaitable

from nonebot import logger

if TYPE_CHECKING:
    from .parsers.base import BaseParser

PlatformName = Literal["netease", "qqmusic", "kugou"]
"""点歌支持的音乐服务标识"""

# 每个服务固定搜索返回条数: 三服务全成功时合计 15 首(全部展示, 不截断)
DEFAULT_PER_SERVICE_LIMIT = 5

# VIP 过滤后补齐用的搜索倍数：先多搜 limit × 此倍数，过滤付费后截断到 limit
_FILTER_SEARCH_MULTIPLIER = 3

# 网易云公开 Web 搜索接口（无需登录/加密）
_NETEASE_SEARCH_API = "https://music.163.com/api/search/get/web"
_NETEASE_HEADERS = {"Referer": "https://music.163.com/"}

# 酷狗公开移动端搜索接口（无签名）
_KUGOU_SEARCH_API = "http://mobilecdn.kugou.com/api/v3/search/song"
_KUGOU_UA = "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36"

# 平台展示名（供渲染和日志使用）
_PLATFORM_DISPLAY: dict[PlatformName, str] = {
    "netease": "网易云",
    "qqmusic": "QQ音乐",
    "kugou": "酷狗",
}


@dataclass(slots=True)
class SearchItem:
    """单首搜索结果候选（跨服务统一结构）。

    Attributes:
        platform: 服务标识（netease/qqmusic/kugou）。
        song_id: 平台内唯一 id —— 网易云/QQ 为数字或 mid 字符串,酷狗为文件 hash。
        name: 歌曲名。
        artist: 歌手名（多个用 ``/`` 拼接）。
        duration: 时长（秒,0 表示未知）。
        pic_url: 封面 URL（可能为空）。
    """

    platform: PlatformName
    song_id: str
    name: str
    artist: str
    duration: float = 0.0
    pic_url: str = ""

    @property
    def platform_display(self) -> str:
        return _PLATFORM_DISPLAY.get(self.platform, self.platform)

    @property
    def display(self) -> str:
        """渲染用一行展示文本。"""
        return f"{self.name} - {self.artist}"


# --------------------------------------------------------------------------- #
# VIP 过滤
# --------------------------------------------------------------------------- #
def _is_paid_filter_enabled(platform: PlatformName) -> bool:
    """该平台是否需要过滤付费歌曲。

    有可用登录态（凭证可用）时返回 False（不过滤，登录后可解析 VIP）；
    无登录态返回 True（过滤掉付费曲，避免选了却解析失败）。

    - netease: ``netease.credential.is_available()``
    - qqmusic: ``qqmusic.credential.is_available()``（缺包视为无登录态）
    - kugou: 暂无登录功能，恒为 True（始终过滤）
    """
    if platform == "netease":
        from .parsers.netease import credential as netease_cred

        return not netease_cred.is_available()
    if platform == "qqmusic":
        try:
            from .parsers.qqmusic import credential as qq_cred

            return not qq_cred.is_available()
        except ImportError:
            return True  # 缺包视为无登录态
    # kugou 暂无登录功能
    return True


def _is_paid_song_netease(raw: dict[str, Any]) -> bool:
    """网易云：fee == 1 为 VIP（过滤）；fee == 8（低音质免费可播）保留；0 免费。"""
    fee = raw.get("fee", 0)
    return fee == 1


def _is_paid_song_kugou(raw: dict[str, Any]) -> bool:
    """酷狗：pay_type == 3 为付费（过滤）；0 免费。"""
    return raw.get("pay_type", 0) == 3


async def search_netease(
    parser: "BaseParser", keyword: str, limit: int = DEFAULT_PER_SERVICE_LIMIT
) -> list[SearchItem]:
    """网易云搜索（公开 Web 接口）。失败静默返回 ``[]``。

    未登录时自动过滤 VIP 歌曲（fee==1），并多搜补齐至 ``limit`` 条免费曲。
    """
    try:
        filter_paid = _is_paid_filter_enabled("netease")
        # 过滤开启时多搜，过滤后仍有足够免费曲可截断到 limit
        search_limit = limit * _FILTER_SEARCH_MULTIPLIER if filter_paid else limit
        resp = await parser.request(
            _NETEASE_SEARCH_API,
            params={"s": keyword, "type": 1, "offset": 0, "limit": search_limit, "httpStatus": 1},
            headers=_NETEASE_HEADERS,
            raise_for_status=False,
        )
        if resp.status_code != 200:
            logger.warning(f"网易云搜索返回 {resp.status_code}")
            return []
        songs = (resp.json().get("result") or {}).get("songs") or []
    except Exception as e:
        logger.debug(f"网易云搜索失败,静默跳过: {e!r}")
        return []

    items: list[SearchItem] = []
    for s in songs:
        try:
            if filter_paid and _is_paid_song_netease(s):
                continue
            artists = s.get("artists") or s.get("singer") or []
            artist = " / ".join(a.get("name", "") for a in artists) or "未知歌手"
            # 搜索接口的封面字段在 album.artist.img1v1Url（小图,解析时 cover_image 会重取大图）
            pic_url = ((s.get("album") or {}).get("artist") or {}).get("img1v1Url", "") or ""
            items.append(
                SearchItem(
                    platform="netease",
                    song_id=str(s.get("id", "")),
                    name=s.get("name", "") or "未知歌曲",
                    artist=artist,
                    duration=(s.get("duration") or 0) / 1000.0,  # ms → s
                    pic_url=pic_url,
                )
            )
        except Exception:
            continue
    return items[:limit]


async def search_qqmusic(
    parser: "BaseParser", keyword: str, limit: int = DEFAULT_PER_SERVICE_LIMIT
) -> list[SearchItem]:
    """QQ 音乐搜索（``qqmusic_api``）。未安装库或失败均静默返回 ``[]``。

    QQ 音乐解析依赖 ``qqmusic-api-python``,缺包时该服务直接降级为空结果,
    不影响网易云/酷狗。未登录时自动过滤付费歌曲（pay.pay_play==1）。
    """
    try:
        from qqmusic_api import Client
    except ImportError:
        logger.debug("qqmusic-api-python 未安装,QQ 音乐搜索降级为空")
        return []

    try:
        filter_paid = _is_paid_filter_enabled("qqmusic")
        search_limit = limit * _FILTER_SEARCH_MULTIPLIER if filter_paid else limit
        async with Client() as client:
            resp = await client.search.general_search(keyword, page=1, num=search_limit)
        # qqmusic_api 的 num 参数实测不生效(固定返回 30 条),这里显式截断
        items_list = (resp.song.items if resp.song else [])[:search_limit]
    except Exception as e:
        logger.debug(f"QQ 音乐搜索失败,静默跳过: {e!r}")
        return []

    items: list[SearchItem] = []
    for it in items_list:
        try:
            if filter_paid and getattr(it, "pay", None) and it.pay.pay_play == 1:
                continue
            singers = [s.name for s in (it.singer or []) if s.name]
            # cover_url 是方法,需调用取值; album 可能为 None
            pic_url = ""
            if it.album and it.album.cover_url:
                cover = it.album.cover_url
                pic_url = cover() if callable(cover) else str(cover)
            items.append(
                SearchItem(
                    platform="qqmusic",
                    song_id=it.mid or "",
                    name=it.name or it.title or "未知歌曲",
                    artist=" / ".join(singers) or "未知歌手",
                    duration=float(it.interval or 0),  # 秒
                    pic_url=pic_url,
                )
            )
        except Exception:
            continue
    return items[:limit]


async def search_kugou(parser: "BaseParser", keyword: str, limit: int = DEFAULT_PER_SERVICE_LIMIT) -> list[SearchItem]:
    """酷狗搜索（公开移动端接口,无签名）。失败静默返回 ``[]``。

    酷狗暂无登录功能，恒过滤付费歌曲（pay_type==3），并多搜补齐至 ``limit`` 条免费曲。
    """
    try:
        filter_paid = _is_paid_filter_enabled("kugou")
        search_limit = limit * _FILTER_SEARCH_MULTIPLIER if filter_paid else limit
        resp = await parser.request(
            _KUGOU_SEARCH_API,
            params={"keyword": keyword, "page": 1, "pagesize": search_limit, "showtype": 10},
            headers={"User-Agent": _KUGOU_UA},
            raise_for_status=False,
        )
        if resp.status_code != 200:
            logger.warning(f"酷狗搜索返回 {resp.status_code}")
            return []
        data = resp.json()
        if data.get("errcode") not in (None, 0, "0"):
            logger.warning(f"酷狗搜索 errcode={data.get('errcode')}")
            return []
        info = (data.get("data") or {}).get("info") or []
    except Exception as e:
        logger.debug(f"酷狗搜索失败,静默跳过: {e!r}")
        return []

    items: list[SearchItem] = []
    for s in info:
        try:
            if filter_paid and _is_paid_song_kugou(s):
                continue
            items.append(
                SearchItem(
                    platform="kugou",
                    song_id=s.get("hash", "") or "",
                    name=s.get("songname", "") or "未知歌曲",
                    artist=s.get("singername", "") or "未知歌手",
                    duration=float(s.get("duration", 0)),  # 秒
                    pic_url="",  # 搜索接口不含封面,解析时再取
                )
            )
        except Exception:
            continue
    return items[:limit]


# 服务标识 → 搜索函数
_SEARCHERS: dict[PlatformName, Callable[[BaseParser, str, int], Awaitable[list[SearchItem]]]] = {
    "netease": search_netease,
    "qqmusic": search_qqmusic,
    "kugou": search_kugou,
}


async def aggregate_search(
    parser: "BaseParser",
    keyword: str,
    platforms: list[PlatformName],
    per_service_limit: int = DEFAULT_PER_SERVICE_LIMIT,
) -> list[SearchItem]:
    """并发搜索多个服务并合并结果。

    单服务失败（异常或返回空）被静默忽略,不向调用方暴露失败原因。
    合并顺序按 ``platforms`` 给定的顺序（默认 ``par点歌`` 时为 网易云 → QQ → 酷狗）。

    每个服务固定搜 ``per_service_limit`` 条,全部展示不截断:
    - 三服务全成功: 3 × 5 = 最多 15 条（去重后实际可能更少）
    - 单服务（``par网易云`` 等）: 5 条
    - 单服务失败: 由其他服务补齐,不向用户暴露失败

    VIP 过滤：未登录的服务自动剔除付费歌曲并多搜补齐（见各 ``search_*``）。

    Args:
        parser: 任意已实例化的 ``BaseParser``,仅用其 ``request`` 封装发 HTTP。
        keyword: 搜索关键词。
        platforms: 要搜索的服务列表。
        per_service_limit: 每个服务搜索返回条数,默认 5。

    Returns:
        合并去重后的 ``SearchItem`` 列表（按平台顺序排列）。
        全部服务失败/无结果时返回空列表（由上层决定是否提示）。
    """
    # 运行时按名字解析函数引用（而非用模块级 _SEARCHERS 字典的快照）,
    # 以便测试用 unittest.mock.patch 替换 search_netease/qqmusic/kugou 生效。
    import sys

    _func_map = {
        "netease": "search_netease",
        "qqmusic": "search_qqmusic",
        "kugou": "search_kugou",
    }
    module = sys.modules[__name__]
    coros = [getattr(module, _func_map[p])(parser, keyword, per_service_limit) for p in platforms if p in _func_map]
    # return_exceptions=True: 单服务异常静默吞掉（双保险,虽然各 search_* 内部已 try/except）
    results = await asyncio.gather(*coros, return_exceptions=True)

    merged: list[SearchItem] = []
    seen: set[tuple[str, str, str]] = set()  # (platform, song_id, name) 粗去重
    for items in results:
        # isinstance(BaseException): 让 pyright 收窄掉 gather(return_exceptions=True)
        # 的所有异常分支(Exception + KeyboardInterrupt/SystemExit), 避免联合类型不可迭代报错
        if isinstance(items, BaseException):
            # 该服务异常,静默跳过(logger.debug 已在各 search_* 内记录,
            # 这里是 gather 层兜底,防止单服务异常炸整个点歌)
            logger.debug(f"aggregate_search: 某服务异常被静默跳过: {items!r}")
            continue
        for it in items:
            key = (it.platform, it.song_id, it.name.strip())
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    return merged
