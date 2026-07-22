"""酷狗音乐 SDK（直连官方接口，纯 Python，无需外部容器）。

接口来自酷狗公开 Web/Mobile 端：
- 歌曲详情：GET m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash=...（无签名，httpx）
- 播放地址：GET gateway.kugou.com/v5/url?hash=...&quality=128（Android MD5 签名 + signKey）
- 歌词：两步 lyrics.kugou.com/search → lyrics.kugou.com/download（无签名，httpx）

签名算法来自 MakcRe/KuGouMusicApi（MIT）的 util/helper.js，标准库 ``hashlib`` 即可。

**播放地址接口的 SSA 反爬**：``gateway.kugou.com/v5/url`` 会按 TLS 指纹触发 SSA 验证
（errcode 20028 + ssa-code 头）。Python ``httpx`` 的 TLS 指纹会被标记，因此播放地址
请求改用 ``curl_cffi``（浏览器指纹模拟，与项目下载模块一致）发起。详情和歌词接口
无此问题，继续用 ``parser.request``（httpx）。VIP/无版权曲目会返回空 url，据此判定不可播。
"""

import time
import base64
import hashlib
from typing import TYPE_CHECKING

from nonebot import logger

from ..config import pconfig

if TYPE_CHECKING:
    from .base import BaseParser

# 签名常量（来自 KuGouMusicApi util/config.json + helper.js，非 lite 模式）
_APPID = 1005
_SALT_ANDROID = "OIlwieks28dk2k092lksi2UIkp"
_SALT_SIGNKEY = "57ae12eb6890223e355ccfcb74edf70d"

# 接口地址
_DETAIL_API = "https://m.kugou.com/app/i/getSongInfo.php"
_PLAY_URL_API = "https://gateway.kugou.com/v5/url"
_LYRIC_SEARCH_API = "https://lyrics.kugou.com/search"
_LYRIC_DOWNLOAD_API = "https://lyrics.kugou.com/download"

# Android 请求头（来自 KuGouMusicApi util/request.js）
_ANDROID_UA = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"


def _sign_android(params: dict, data: str = "") -> str:
    """Android MD5 签名：按 key 字典序拼 k=v，前后包盐。"""
    ps = "".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.md5(f"{_SALT_ANDROID}{ps}{data}{_SALT_ANDROID}".encode()).hexdigest()


def _sign_key(hash_: str, mid: str, userid: int = 0) -> str:
    """song_url 专用 key 字段签名：MD5(hash + salt + appid + mid + userid)。"""
    return hashlib.md5(f"{hash_}{_SALT_SIGNKEY}{_APPID}{mid}{userid}".encode()).hexdigest()


async def get_song_detail(parser: "BaseParser", song_hash: str) -> dict:
    """获取歌曲详情：标题、歌手、封面URL、时长（秒）。

    使用酷狗移动端 ``getSongInfo`` 接口（无需签名）。返回 ``{name, author, pic_url,
    duration, album_id}``，找不到返回空 dict。
    """
    resp = await parser.request(
        _DETAIL_API,
        params={"cmd": "playInfo", "hash": song_hash},
        headers={"User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36"},
        raise_for_status=False,
    )
    if resp.status_code != 200:
        logger.warning(f"酷狗详情接口返回 {resp.status_code}")
        return {}
    data = resp.json()
    if not data or data.get("errcode") != 0:
        logger.warning(f"酷狗详情 errcode={data.get('errcode') if data else '空'}")
        return {}
    return {
        "name": data.get("songName", "") or "未知歌曲",
        "author": data.get("singerName", "") or "未知歌手",
        "pic_url": data.get("imgUrl", "") or "",
        "duration": int(data.get("timeLength", 0)),  # 秒
        "album_id": data.get("albumid", "") or "",
    }


async def get_play_url(parser: "BaseParser", song_hash: str, quality: str = "128") -> str | None:
    """获取真实音频地址（Android 签名 + signKey，经 curl_cffi 绕过 SSA TLS 指纹检测）。

    被 SSA 风控、VIP/无版权时返回 None。``quality`` 支持 128/320/flac/high 等，默认 128。

    SSA（errcode 20028）是酷狗按 TLS 指纹触发的反爬：httpx 的指纹被标记，``curl_cffi``
    模拟浏览器指纹可绕过。curl_cffi 不可用时回退 httpx（会被拦截）。
    """
    song_hash = song_hash.lower()
    # request.js 默认值：无 cookie 时 dfid='-', mid='undefined'(模板字符串), uuid='-'
    dfid = "-"
    mid = "undefined"
    userid = 0
    clienttime = int(time.time())

    # 参数来自 KuGouMusicApi module/song_url.js dataMap + request.js defaultParams
    # song_url.js 覆盖 clientver 为 11430
    params: dict[str, str | int] = {
        "appid": _APPID,
        "clientver": 11430,
        "clienttime": clienttime,
        "mid": mid,
        "uuid": "-",
        "dfid": dfid,
        "album_id": 0,
        "area_code": 1,
        "hash": song_hash,
        "ssa_flag": "is_fromtrack",
        "version": 11430,
        "page_id": 151369488,
        "quality": quality,
        "album_audio_id": 0,
        "behavior": "play",
        "pid": 2,
        "cmd": 26,
        "pidversion": 3001,
        "IsFreePart": 0,
        "ppage_id": "463467626,350369493,788954147",
        "cdnBackup": 1,
        "module": "",
    }
    # encryptKey: true → key 参与 signature 计算
    params["key"] = _sign_key(song_hash, mid, userid)
    # encryptType: 'android' → signature
    params["signature"] = _sign_android(params)

    # headers: request.js 也会在 header 里带 dfid/clienttime/mid
    headers = {
        "User-Agent": _ANDROID_UA,
        "dfid": dfid,
        "clienttime": str(clienttime),
        "mid": mid,
        "kg-rc": "1",
        "kg-thash": "5d816a0",
        "kg-rec": "1",
        "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
        "x-router": "trackercdn.kugou.com",
    }

    # 优先 curl_cffi（绕过 SSA TLS 指纹检测），不可用回退 httpx
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        AsyncSession = None  # type: ignore[assignment]

    if AsyncSession is not None:
        import json as _json
        import asyncio as _asyncio

        # 酷狗在国内，默认不走代理（与 NGA 一致）；用户显式配置了代理才走，
        # 避免被容器层 HTTP_PROXY/HTTPS_PROXY 环境变量错误地经代理。
        proxy_url = pconfig.proxy
        proxies = {"https": proxy_url, "http": proxy_url} if proxy_url else {"https": "", "http": ""}

        async def _do_request():
            async with AsyncSession(impersonate="chrome110", timeout=15, verify=False) as session:
                return await session.get(
                    _PLAY_URL_API,
                    params=params,
                    headers=headers,
                    proxies=proxies,  # type: ignore[arg-type]
                )

        try:
            resp = await _asyncio.wait_for(_do_request(), timeout=15)
        except Exception as exc:
            logger.warning(f"酷狗 get_play_url(curl_cffi) 异常: {exc!r}")
            return None
        if resp.headers.get("ssa-code"):
            logger.debug(f"酷狗 play 接口(curl_cffi)命中 SSA 风控, ssa-code={resp.headers.get('ssa-code')}")
            return None
        if resp.status_code != 200:
            logger.warning(f"酷狗 play 接口(curl_cffi)返回 {resp.status_code}")
            return None
        try:
            data = _json.loads(resp.text)
        except _json.JSONDecodeError:
            logger.warning(f"酷狗 play 响应(curl_cffi)非 JSON: {resp.text[:200]!r}")
            return None
        if data.get("errcode") not in (None, 0, "0"):
            logger.debug(f"酷狗 play errcode={data.get('errcode')} (VIP/无版权)")
            return None
        return _extract_url(data)

    # 回退：httpx（大概率被 SSA 拦截）
    try:
        resp = await parser.request(_PLAY_URL_API, params=params, headers=headers, raise_for_status=False)
    except Exception as exc:
        logger.warning(f"酷狗 get_play_url(httpx) 异常: {exc!r}")
        return None
    if resp.headers.get("ssa-code"):
        logger.debug(f"酷狗 play 接口(httpx)命中 SSA 风控, ssa-code={resp.headers.get('ssa-code')}")
        return None
    if resp.status_code != 200:
        logger.warning(f"酷狗 play 接口(httpx)返回 {resp.status_code}")
        return None
    try:
        data = resp.json()
    except Exception as exc:
        logger.warning(f"酷狗 play 响应(httpx)非 JSON: {exc!r}")
        return None
    if data.get("errcode") not in (None, 0, "0"):
        logger.debug(f"酷狗 play errcode={data.get('errcode')} (VIP/无版权)")
        return None
    return _extract_url(data)


def _extract_url(data: dict) -> str | None:
    """从 v5/url 响应提取播放地址。

    url 字段格式可能是：
    - str：直接是音频地址
    - list[str]：多个 CDN 地址，取第一个
    - list[dict]：[{hash, url, ...}, ...]，取第一个的 url
    """
    raw_url = data.get("url")
    if isinstance(raw_url, list):
        if not raw_url:
            return None
        first = raw_url[0]
        if isinstance(first, str):
            return first or None
        if isinstance(first, dict):
            return first.get("url") or None
        return None
    return raw_url or None


async def get_lyric(parser: "BaseParser", song_hash: str, duration: int = 0) -> str:
    """两步获取歌词（LRC 文本）：search 拿 id+accesskey → download 拿 base64 → 解码。

    无歌词返回空串。
    """
    # Step A: 搜索歌词候选
    resp = await parser.request(
        _LYRIC_SEARCH_API,
        params={
            "hash": song_hash,
            "duration": duration,
            "keyword": "",
            "lrctxt": "1",
            "client": "pc",
            "ver": "1",
            "man": "no",
        },
        raise_for_status=False,
    )
    if resp.status_code != 200:
        logger.warning(f"酷狗歌词搜索接口返回 {resp.status_code}")
        return ""
    candidates = (resp.json().get("candidates")) or []
    if not candidates:
        return ""
    hit = candidates[0]
    lyric_id = hit.get("id")
    accesskey = hit.get("accesskey")
    if not lyric_id or not accesskey:
        return ""

    # Step B: 下载歌词（base64 编码的 LRC 文本）
    resp = await parser.request(
        _LYRIC_DOWNLOAD_API,
        params={
            "ver": "1",
            "client": "pc",
            "id": lyric_id,
            "accesskey": accesskey,
            "fmt": "lrc",
            "charset": "utf8",
        },
        raise_for_status=False,
    )
    if resp.status_code != 200:
        logger.warning(f"酷狗歌词下载接口返回 {resp.status_code}")
        return ""
    try:
        content = resp.json().get("content")
    except Exception as exc:
        logger.warning(f"酷狗歌词下载响应非 JSON: {exc!r}")
        return ""
    if not content:
        return ""
    try:
        return base64.b64decode(content).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"酷狗歌词 base64 解码失败: {e!r}")
        return ""
