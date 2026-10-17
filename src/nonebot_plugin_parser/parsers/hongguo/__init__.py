"""红果短剧（抖音旗下免费短剧平台）解析。

分享形态与解析链路（2026-10 逆向并实测）：

- App 分享文本: ``漫剧《剧名》 - 免费好剧，尽在红果点击链接打开
  https://novelquickapp.com/s/{短码}/ 复制本条消息后，打开「红果短剧App」后免费看全集~``
- 短码 302 到 ``novelquickapp.com/hongguo/ug/pages/video-*-share``，跳转
  Location 的 ``zlink`` 参数内嵌 ``schemeParams`` JSON（含 ``video_series_id``
  与当前分集 ``vid``；漫剧类分享 series_id 可能为空）。
- 剧集元数据: 网页版 ``hongguoduanju.com/detail?series_id=`` 的 SSR 数据
  （``window._ROUTER_DATA``，与抖音分享页同模式），含标题/封面/简介/全集
  ``vid_list``。游客仅开放前 ``accessible_episode_cnt``（通常 3）集的网页
  播放；完整分集列表不受限。
- 分集播放（2026-10-17 起主力渠道）: 番茄小说系**游客**批量播放接口
  ``api5-normal-sinfonlinec.fqnovel.com/novel/player/multi_video_model/v1/``，
  随机 18 位 iid/device_id 直连，**免设备注册、免任何签名头**，body 以
  ``mixed_video_id_map={"1004": [vid]}`` 携带分集，全集可用（2026-10-10 起的
  ``video_model/v1`` 注册设备 + X-Gorgon 形态自 10-17 上游收紧后一律 200 空
  body，保留作次选兜底）；返回 CENC 加密流 + ``spade_a`` 密钥材料（见
  :mod:`._crypto`），用 ffmpeg ``-decryption_key`` 流复制解密。
- 兜底: 网页版 ``/player/{series_id}/{vid}`` 的 SSR 数据对可开放分集直接
  给出无加密 mp4 直链（无需签名与 ffmpeg）；2026-10-17 起网页端同样启用
  播放保护（SSR 仅下发 ``protectedPlayback=true`` + SEO 数据），该兜底
  仅在保护回退时生效。

协议逆向参考: zhoufuweigg/guoapp（Go 站源实现）与
lingbol088-spec/short-drama-downloader（Python 设备注册/签名/解密管线）。
"""

import re
import json
import time
import base64
import asyncio
import secrets
from html import unescape
from typing import ClassVar
from hashlib import md5
from pathlib import Path
from dataclasses import field, dataclass
from urllib.parse import quote, unquote, parse_qs, urlparse

from nonebot import logger

from . import _device
from ..base import Platform, BaseParser, PlatformEnum, ParseException, handle
from ._sign import sign_request
from ...utils import safe_unlink, exec_ffmpeg_cmd, ffmpeg_available
from ._crypto import derive_content_key
from ...config import pconfig
from ...download import DOWNLOADER
from ...exception import TipException, IgnoreException, DownloadException

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_WEB_BASE = "https://hongguoduanju.com"
_APP_BASE = "https://api5-normal-sinfonlineb.fqnovel.com"
# 2026-10 改版后的游客批量播放端点（host 与旧 video_model 的 sinfonineb 不同）
_GUEST_PLAYBACK_URL = "https://api5-normal-sinfonlinec.fqnovel.com/novel/player/multi_video_model/v1/"

_APP_UA = _device.APP_UA

# App 加密流下载头（Referer 为番茄系站点，guoapp 同款）
_APP_STREAM_HEADERS = {"User-Agent": _APP_UA, "Referer": "https://novel.snssdk.com/"}
_WEB_STREAM_HEADERS = {"User-Agent": _APP_UA, "Referer": f"{_WEB_BASE}/"}

_ROUTER_DATA_RE = re.compile(r"(?:window\.)?_ROUTER_DATA\s*=\s*")
# 分享 H5 页内嵌的 applink（schemeParams 单层编码；\u002F 为 JS 转义的斜杠）
_EMBEDDED_APPLINK_RE = re.compile(r"applink\.novelquickapp\.com(?:/|\\u002F)+dVu4P\?schemeParams=([^\"'\s<]+)")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class _Series:
    """剧集元数据（网页 detail 或分享页二选一来源）"""

    title: str
    series_id: str = ""
    intro: str = ""
    cover: str = ""
    category: str = ""
    episode_cnt: int = 0
    vid_list: list[str] = field(default_factory=list)


@dataclass
class _Media:
    """单集播放信息"""

    url: str
    duration: float = 0.0
    key_hex: str | None = None
    """CENC 内容密钥 hex；None 表示明文流"""
    backup_urls: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _parse_router_data(text: str) -> dict | None:
    """从 SSR 页面提取 ``_ROUTER_DATA`` JSON（同抖音分享页模式）。"""
    if (matched := _ROUTER_DATA_RE.search(text)) is None:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[matched.end() :])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _find_page_data(html_text: str) -> dict | None:
    """从分享 H5 页提取内嵌 pageData（含 series_data / chapter_ids 等）。"""
    for matched in re.finditer(r'data-fn-args="([^"]*)"', html_text):
        try:
            args = json.loads(unescape(matched.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(args, list) and len(args) >= 3 and isinstance(args[2], dict) and "series_data" in args[2]:
            return args[2]
    return None


def _parse_scheme_params(raw: str) -> dict:
    """解析 schemeParams JSON（容忍多层 URL 编码）。"""
    value = raw
    for _ in range(3):
        value = value.strip()
        if value.startswith("{"):
            break
        value = unquote(value)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_ids_from_params(params: dict) -> tuple[str, str]:
    """从 schemeParams 提取 (series_id, vid)。"""
    series_id = str(params.get("video_series_id") or "")
    vid = str(params.get("vid") or params.get("video_id") or params.get("share_toast_vid") or "")
    return (series_id if series_id.isdigit() else "", vid if vid.isdigit() else "")


def _safe_int(value: object, default: int = 0) -> int:
    """宽容解析整数（外部接口的计数字段可能给字符串/浮点串/脏值）。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _decode_maybe_base64(value: str) -> str:
    """部分接口的媒体地址字段是 Base64 编码的 URL。"""
    text = value.strip()
    if text.startswith("http"):
        return text
    padding = -len(text) % 4
    try:
        decoded = base64.b64decode(text + "=" * padding).decode("utf-8", "replace").strip()
    except Exception:
        return ""
    return decoded if decoded.startswith("http") else ""


def _media_addresses(info: dict) -> list[str]:
    """从 video_list 变体 / 播放信息中收集可用的媒体地址（guoapp 同款字段集）。"""
    addresses: list[str] = []
    for key in ("main_url", "backup_url", "backup_url_1", "backup_url_2", "backup_urls", "url_list"):
        value = info.get(key)
        if isinstance(value, str):
            if url := _decode_maybe_base64(value):
                if url not in addresses:
                    addresses.append(url)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and (url := _decode_maybe_base64(item)) and url not in addresses:
                    addresses.append(url)
    return addresses


def _guest_playback_request(vids: list[str]) -> tuple[str, dict[str, str], bytes]:
    """构造游客态 ``multi_video_model`` 请求的 ``(url, headers, body)``。

    形态逆向自 hongguo-mac v0.8.1 的取流实现（Backend/hongguo.py video_models，
    2026-10-17 实测 code=0 全清晰度下发，第 1/51 集均通）：

    - 身份字段用随机 18 位数字，服务端不校验注册态，也不需要任何签名头
      （无 X-Gorgon/X-Argus/Cookie，与旧 ``video_model/v1`` 的根本区别）;
    - version 固定 71332 (7.1.3.32) 且 ``pv_player``/``player_so_load`` 等
      播放器参数必须携带;
    - body 以 ``mixed_video_id_map={"1004": vids}`` 携带分集（单次至多 5 个），
      ``biz_param`` 的 ``device_level``/``video_platform`` 与旧接口取值不同;
    - query 手拼原样发送（``*`` 不转义，与 ``_app_request`` 同款，均被接受）。
    """
    now_ms = int(time.time() * 1000)
    query = {
        "iid": str(10**17 + secrets.randbelow(9 * 10**17)),
        "device_id": str(10**17 + secrets.randbelow(9 * 10**17)),
        "ac": "wifi",
        "channel": "update_64",
        "aid": "8662",
        "app_name": "novelread",
        "version_code": "71332",
        "version_name": "7.1.3.32",
        "device_platform": "android",
        "os": "android",
        "ssmix": "a",
        "device_type": _device.DEVICE_TYPE,
        "device_brand": _device.DEVICE_BRAND,
        "language": "zh",
        "os_api": _device.OS_API,
        "os_version": _device.OS_VERSION,
        "manifest_version_code": "71332",
        "resolution": _device.RESOLUTION,
        "dpi": _device.DPI,
        "update_version_code": "71332",
        "host_abi": "arm64-v8a",
        "dragon_device_type": "phone",
        "pv_player": "71332",
        "compliance_status": "0",
        "need_personal_recommend": "1",
        "player_so_load": "1",
        "is_android_pad_screen": "0",
        "_rticket": str(now_ms),
    }
    query_string = "&".join(f"{key}={value}" for key, value in query.items())
    headers = {
        "User-Agent": _APP_UA,
        "Accept": "application/json; charset=utf-8,application/x-protobuf",
        "Content-Type": "application/json; charset=UTF-8",
        "x-xs-from-web": "0",
        "x-ss-req-ticket": str(now_ms),
        "x-tt-request-tag": "t=0;n=0",
        "sdk-version": "2",
        "passport-sdk-version": "50561",
        "x-vc-bdturing-sdk-version": "3.7.2.cn",
    }
    body = json.dumps(
        {
            "biz_param": {
                "detail_page_version": 0,
                "device_level": 3,
                "disable_digg_stat": False,
                "need_all_video_definition": True,
                "need_mp4_align": False,
                "use_os_player": False,
                "use_server_dns": False,
                "video_platform": 1024,
            },
            "mixed_video_id_map": {"1004": vids},
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return f"{_GUEST_PLAYBACK_URL}?{query_string}", headers, body


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class HongguoParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name=PlatformEnum.HONGGUO, display_name="红果短剧")

    # -- URL 匹配 -----------------------------------------------------------

    # App 分享短链（主形态）: https://novelquickapp.com/s/{code}/
    @handle("novelquickapp.com/s/", r"novelquickapp\.com/s/[A-Za-z0-9_\-]+/?")
    # 分享 H5 中间页: https://novelquickapp.com/hongguo/ug/pages/video-*-share?...
    @handle("novelquickapp.com/hongguo", r"novelquickapp\.com/hongguo/ug/pages/[^\s\"'<>，。]+")
    async def _parse_share_link(self, searched: re.Match[str]):
        return await self._parse_share_url(f"https://{searched.group(0)}")

    # applink 深链（schemeParams 直接在 query 里）
    @handle("applink.novelquickapp", r"applink\.novelquickapp\.com/[^\s\"'<>，。]+")
    async def _parse_applink(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        params: dict = {}
        for key, values in parse_qs(urlparse(url).query).items():
            if key == "schemeParams" and values:
                params = _parse_scheme_params(values[0])
        series_id, vid = _extract_ids_from_params(params)
        if not vid and not series_id:
            raise ParseException(f"红果 applink 中未找到剧集标识: {url[:80]}")
        return await self._build_result(series_id, vid, None, url)

    # 网页版链接: /detail?series_id=、/player/{sid}/{vid}、/video/{vid}
    @handle("hongguoduanju.com", r"hongguoduanju\.com/[^\s\"'<>，。]+")
    async def _parse_web_url(self, searched: re.Match[str]):
        url = f"https://{searched.group(0)}"
        series_id, vid = "", ""
        if matched := re.search(r"/player/(\d+)/(\d+)", url):
            series_id, vid = matched.group(1), matched.group(2)
        elif matched := re.search(r"series_id=(\d+)", url):
            series_id = matched.group(1)
        elif matched := re.search(r"/video/(\d+)", url):
            vid = matched.group(1)
        elif matched := re.search(r"video_id=(\d+)", url):
            vid = matched.group(1)
        if not series_id and not vid:
            raise ParseException(f"红果网页链接中未找到剧集标识: {url[:80]}")
        return await self._build_result(series_id, vid, None, url)

    # 纯文本分享（链接被删去）: 漫剧《剧名》 - 免费好剧，尽在红果
    @handle("免费好剧，尽在红果", r"《(?P<title>[^》]{1,60})》[^\n]*?免费好剧，尽在红果")
    async def _parse_share_text(self, searched: re.Match[str]):
        title = searched.group("title").strip()
        if not title:
            raise ParseException("红果分享文本中未找到剧名")
        series_id = await self._search_series_id(title)
        if not series_id:
            raise ParseException(f"红果站内未找到短剧《{title}》，请确认分享内容包含链接")
        return await self._build_result(series_id, "", None, f"{_WEB_BASE}/detail?series_id={series_id}")

    # -- 分享短链解析 --------------------------------------------------------

    async def _parse_share_url(self, url: str):
        series_id, vid = "", ""
        # 1. 短链 302 的 Location 里 zlink 内嵌 schemeParams（最快路径）
        try:
            location = await self.get_redirect_url(url)
            if location != url:
                zlink = parse_qs(urlparse(location).query).get("zlink", [""])[0]
                if zlink:
                    values = parse_qs(urlparse(zlink).query).get("schemeParams", [])
                    if values:
                        series_id, vid = _extract_ids_from_params(_parse_scheme_params(values[0]))
        except Exception as e:
            logger.debug(f"hongguo 短链重定向解析失败，回退页面数据: {url[:80]} ({e!r})")

        # 2. 未拿到标识时抓分享 H5 页：内嵌 applink schemeParams + pageData
        share_page: dict | None = None
        if not vid and not series_id:
            fetched = await self._fetch_share_page(url)
            if fetched is not None:
                share_page, page_html = fetched
                embedded = _EMBEDDED_APPLINK_RE.search(page_html)
                if embedded:
                    series_id, vid = _extract_ids_from_params(_parse_scheme_params(embedded.group(1)))
                if not vid:
                    # 漫剧分享页 chapter 首集兜底
                    chapter_ids = share_page.get("chapter_ids")
                    if isinstance(chapter_ids, list) and chapter_ids:
                        vid = str(chapter_ids[0])
        elif not vid or not series_id:
            # 有 id 但缺元数据来源时也抓 pageData（章节列表 / 标题）
            fetched = await self._fetch_share_page(url)
            if fetched is not None:
                share_page = fetched[0]

        if not vid and not series_id:
            raise ParseException(f"红果分享链接解析失败（未找到剧集标识）: {url[:80]}")
        return await self._build_result(series_id, vid, share_page, url)

    async def _fetch_share_page(self, url: str) -> tuple[dict, str] | None:
        """抓取分享 H5 页，返回 (pageData, 原始 HTML)（HTML 供内嵌 applink 提取）。"""
        try:
            resp = await self.request(url, headers=self.android_headers, follow_redirects=True, raise_for_status=False)
        except Exception as e:
            logger.warning(f"hongguo 分享页请求失败: {url[:80]} ({e!r})")
            return None
        if resp.status_code != 200 or not resp.content:
            logger.warning(f"hongguo 分享页响应异常: {url[:80]} HTTP {resp.status_code}")
            return None
        page = _find_page_data(resp.text)
        if page is None:
            return None
        return page, resp.text

    # -- 剧集元数据 ----------------------------------------------------------

    async def _fetch_series(self, series_id: str) -> _Series:
        """网页版 detail SSR 数据 → 剧集元数据 + 全集 vid_list。"""
        resp = await self.request(f"{_WEB_BASE}/detail?series_id={series_id}", raise_for_status=False)
        if resp.status_code != 200:
            raise ParseException(f"红果剧集详情不可访问（HTTP {resp.status_code}）: {series_id}")
        data = _parse_router_data(resp.text)
        detail = (data or {}).get("loaderData", {}).get("detail_page", {}).get("seriesDetail")
        if not isinstance(detail, dict) or not detail:
            raise ParseException(f"红果剧集详情数据为空（可能已下架）: {series_id}")

        vid_list = [v for v in detail.get("vid_list") or [] if str(v).isdigit()]
        title = str(detail.get("series_name") or detail.get("series_title") or "").strip() or series_id
        if not vid_list:
            raise ParseException(f"红果短剧《{title}》没有可播放的分集")
        return _Series(
            title=title,
            series_id=str(detail.get("series_id") or series_id),
            intro=str(detail.get("series_intro") or "").strip(),
            cover=str(detail.get("series_cover") or "").strip(),
            category=str(detail.get("category_name") or "").strip(),
            episode_cnt=_safe_int(detail.get("episode_cnt"), len(vid_list)),
            vid_list=vid_list,
        )

    async def _series_from_share_page(self, page: dict) -> _Series | None:
        """分享页 pageData → 剧集元数据（漫剧分享无 series_id 时的唯一来源）。"""
        data = page.get("series_data")
        if not isinstance(data, dict):
            return None
        title = str(data.get("title") or "").strip()
        if not title:
            return None
        chapter_ids = [str(v) for v in page.get("chapter_ids") or [] if str(v).isdigit()]
        return _Series(
            title=title,
            intro=str(data.get("series_intro") or "").strip(),
            cover=str(data.get("series_cover") or "").strip(),
            category=str(data.get("category") or "").strip(),
            episode_cnt=_safe_int(data.get("serial_count"), len(chapter_ids)),
            vid_list=chapter_ids,
        )

    async def _search_series_id(self, title: str) -> str:
        """网页版搜索剧名 → series_id（精确匹配优先）。"""
        resp = await self.request(f"{_WEB_BASE}/search/{quote(title)}", raise_for_status=False)
        if resp.status_code != 200:
            return ""
        data = _parse_router_data(resp.text)

        def _find_search_list(obj: object) -> list | None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == "searchList" and isinstance(value, list):
                        return value
                    if (found := _find_search_list(value)) is not None:
                        return found
            elif isinstance(obj, list):
                for item in obj:
                    if (found := _find_search_list(item)) is not None:
                        return found
            return None

        items = _find_search_list((data or {}).get("loaderData")) or []
        fallback = ""
        for item in items:
            video_data = item.get("video_data") if isinstance(item, dict) else None
            if not isinstance(video_data, dict):
                continue
            series_id = str(video_data.get("series_id") or "")
            if not series_id.isdigit():
                continue
            if str(video_data.get("series_title") or "").strip() == title:
                return series_id
            fallback = fallback or series_id
        return fallback

    # -- 播放地址 ------------------------------------------------------------

    async def _resolve_media(self, vid: str, series_id: str) -> _Media:
        """解析单集播放地址：游客接口（全集，2026-10 起主力）→ 注册设备 App 接口
        → 网页 player 页（可开放分集）。"""
        errors: list[str] = []
        try:
            return await self._media_via_guest(vid)
        except (IgnoreException, TipException):
            raise
        except Exception as e:
            errors.append(f"游客接口: {e}")
            logger.warning(f"hongguo 游客取流失败 vid={vid}: {e!r}")

        try:
            return await self._media_via_app(vid)
        except (IgnoreException, TipException):
            raise
        except Exception as e:
            errors.append(f"App 接口: {e}")
            logger.warning(f"hongguo App 取流失败 vid={vid}: {e!r}")

        if series_id:
            try:
                return await self._media_via_web(series_id, vid)
            except (IgnoreException, TipException):
                # 「仅 App 可看」等语义性结果: parse_retry 不重试、matchers 发友好提示
                raise
            except Exception as e:
                errors.append(f"网页取流: {e}")
                logger.warning(f"hongguo 网页取流失败 vid={vid}: {e!r}")

        raise ParseException(f"红果视频地址获取失败（{'；'.join(errors) or '无可用渠道'}）")

    async def _app_request(self, path: str, payload: dict) -> dict | None:
        """签名调用红果 App 接口；HTTP 200 但空 body（风控形态）返回 None。"""
        device_id, install_id = await _device.get_device()
        query = {
            "aid": "8662",
            "app_name": "novelread",
            "version_code": _device.VERSION_CODE,
            "version_name": _device.VERSION_NAME,
            "manifest_version_code": _device.VERSION_CODE,
            "update_version_code": _device.VERSION_CODE,
            "channel": _device.CHANNEL,
            "device_platform": "android",
            "os": "android",
            "ssmix": "a",
            "device_type": _device.DEVICE_TYPE,
            "device_brand": _device.DEVICE_BRAND,
            "language": "zh",
            "os_api": _device.OS_API,
            "os_version": _device.OS_VERSION,
            "resolution": _device.RESOLUTION,
            "dpi": _device.DPI,
            "ac": "wifi",
            "device_id": device_id,
            "iid": install_id,
        }
        query["_rticket"] = str(int(time.time() * 1000))
        query_string = "&".join(f"{key}={value}" for key, value in query.items())
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {
            "User-Agent": _APP_UA,
            "Accept": "application/json",
            "X-XS-From-Web": "0",
            "Sdk-Version": "2",
            "Content-Type": "application/json; charset=utf-8",
            **sign_request(query_string, body, time.time()),
        }
        # 签名基于手拼的原始 query 字节，URL 必须原样发出（本仓 self.request
        # 即 httpx 直连 trust_env=False，与独立 client 等价）
        resp = await self.request(
            f"{_APP_BASE}{path}?{query_string}",
            method="POST",
            content=body,
            headers=headers,
            raise_for_status=False,
        )
        if resp.status_code != 200 or not resp.content:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        code = str(data.get("code", data.get("status_code", "0")))
        if code not in ("", "0"):
            raise ParseException(f"红果 App 接口返回错误码 {code}")
        return data

    def _media_from_video_model(self, model: dict | str | None) -> _Media:
        """从 video_model 载荷选取最高清晰度轨道（游客/App 两渠道共用）。

        轨道结构与 2026-10 改版前一致: ``video_list`` (dict 或 list 变体) 每项
        含 main_url/backup_url（可 base64）+ video_meta + encrypt_info.spade_a。
        """
        if model is None:
            raise ParseException("红果播放数据缺失")
        if isinstance(model, str):
            try:
                model = json.loads(model)
            except json.JSONDecodeError as e:
                raise ParseException("红果播放数据格式异常") from e
        if not isinstance(model, dict):
            raise ParseException("红果播放数据缺失")

        rows = model.get("video_list")
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not isinstance(rows, list) or not rows:
            raise ParseException("红果该集没有可用的播放数据")

        try:
            duration = float(model.get("video_duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        best: _Media | None = None
        best_height = 0
        key_error: Exception | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            meta = row.get("video_meta") or {}
            addresses = _media_addresses(row)
            if not addresses:
                continue
            encrypt = row.get("encrypt_info") or {}
            spade = str(encrypt.get("spade_a") or "")
            key_hex: str | None = None
            if spade or encrypt.get("encrypt") or encrypt.get("encryption_method"):
                try:
                    key_hex = derive_content_key(spade).hex()
                except ValueError as e:
                    key_error = e
                    continue
            try:
                height = int(meta.get("vheight") or meta.get("vwidth") or 0)
            except (TypeError, ValueError):
                height = 0
            # height 为 0 (字段缺失) 时仍取该行: 外部接口形状不能假设 vheight 恒存在,
            # 全部丢弃会让 App 渠道整体失效并误报「未返回媒体地址」
            if best is None or height > best_height:
                best = _Media(
                    url=addresses[0],
                    duration=duration,
                    key_hex=key_hex,
                    backup_urls=addresses[1:],
                    headers=_APP_STREAM_HEADERS,
                )
                best_height = height
        if best is None:
            if key_error is not None:
                raise ParseException(f"红果媒体密钥不可用: {key_error}")
            raise ParseException("红果该集未返回可用的媒体地址")

        if best.key_hex and not ffmpeg_available():
            # 解密链路必需 ffmpeg；无可解密手段时交给上层回退网页直链
            raise DownloadException("红果加密视频需要 ffmpeg 解密，但 ffmpeg 不可用")
        return best

    async def _media_via_guest(self, vid: str) -> _Media:
        """游客批量播放接口取流（2026-10 改版后主力渠道，免签名免设备注册）。

        2026-10-17 上游收紧后，旧 ``video_model/v1``（注册设备 + X-Gorgon）一律
        200 空 body、网页 SSR 只下发 protectedPlayback 标记；游客态
        ``multi_video_model/v1`` 对随机设备标识直接下发全清晰度加密流（含
        spade_a），第 1/51 集实测均通，解密链不变。请求构造见
        :func:`_guest_playback_request`。
        """
        url, headers, body = _guest_playback_request([vid])
        resp = await self.request(
            url,
            method="POST",
            content=body,
            headers=headers,
            raise_for_status=False,
        )
        if resp.status_code != 200 or not resp.content:
            raise ParseException(f"红果游客播放接口无有效响应（HTTP {resp.status_code}）")
        try:
            payload = resp.json()
        except ValueError as e:
            raise ParseException("红果游客播放接口返回非 JSON") from e
        code = payload.get("Code", payload.get("code", 0))
        if code not in ("", 0, "0"):
            raise ParseException(f"红果游客播放接口错误码 {code}")
        entry = (payload.get("data") or {}).get(vid)
        model = entry.get("video_model") if isinstance(entry, dict) else None
        if not model:
            raise ParseException("红果游客播放接口未返回该集播放数据")
        return self._media_from_video_model(model)

    async def _media_via_app(self, vid: str) -> _Media:
        """旧注册设备 App 播放接口取流（2026-10-17 起被上游收紧为 200 空 body，
        降为次选兜底，若上游回退签名策略仍可用）。"""
        payload = {
            "video_id": vid,
            "content_type": 1,
            "biz_param": {"need_all_video_definition": True, "video_platform": 3},
        }
        data = await self._app_request("/novel/player/video_model/v1/", payload)
        if data is None:
            # 200 + 空 body = 设备被风控 / 签名失效，换新设备重试一次
            logger.info("hongguo App 接口空响应，换新设备重试")
            await _device.refresh_device()
            data = await self._app_request("/novel/player/video_model/v1/", payload)
        if data is None:
            raise ParseException("红果播放接口无有效响应")

        return self._media_from_video_model((data.get("data") or {}).get("video_model"))

    async def _media_via_web(self, series_id: str, vid: str) -> _Media:
        """网页 player 页取流（仅游客可开放的分集有直链，无加密）。"""
        resp = await self.request(
            f"{_WEB_BASE}/player/{series_id}/{vid}",
            headers={"User-Agent": _APP_UA, "Referer": f"{_WEB_BASE}/"},
            raise_for_status=False,
        )
        if resp.status_code != 200:
            raise ParseException(f"该集不在网页可看范围（HTTP {resp.status_code}）")
        data = _parse_router_data(resp.text)
        page = None
        for key, value in (data or {}).get("loaderData", {}).items():
            if key.startswith("player_") and isinstance(value, dict) and value.get("video_player_info"):
                page = value
        if page is None:
            raise ParseException("红果网页播放数据为空")
        if str(page.get("vid") or "") != vid:
            # SSR 返回了别的分集（典型为试看替换），不能冒充目标分集发送
            raise TipException("该集仅限红果 App 内观看，网页未开放")
        addresses = _media_addresses(page.get("video_player_info") or {})
        if not addresses:
            raise ParseException("红果网页未提供该集播放地址")
        try:
            duration = float((page.get("video_player_info") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return _Media(url=addresses[0], duration=duration, backup_urls=addresses[1:], headers=_WEB_STREAM_HEADERS)

    # -- 下载与解密 ----------------------------------------------------------

    async def _download_and_decrypt(self, media: _Media) -> Path:
        """下载（必要时 CENC 解密）单集视频，返回可播放的 mp4 路径。"""
        enc_path = await DOWNLOADER.download_video(media.url, ext_headers=media.headers, backup_urls=media.backup_urls)
        if media.key_hex is None:
            return enc_path

        output = pconfig.cache_dir / f"hgdec-{md5(media.url.encode()).hexdigest()[:16]}.mp4"
        if output.exists() and output.stat().st_size > 0:
            # 命中解密缓存时刚下载的加密副本同样是一次性签名直链，不清理会白占缓存目录
            await safe_unlink(enc_path)
            return output

        # 先写临时名再原子替换: 任务被取消/超时(发送层 video_send_timeout)时
        # CancelledError 绕过下面的 except 分支, 半截 ffmpeg 产物若直接落最终路径,
        # 会被同 URL 的下一次解析当有效缓存发出。临时名保留 .mp4 后缀
        # (ffmpeg 按后缀选 muxer, 用 .tmp 会报 "Unable to find a suitable output format")
        tmp_output = output.with_name(f"{output.stem}.tmp.mp4")
        try:
            await exec_ffmpeg_cmd(
                [
                    "ffmpeg",
                    "-y",
                    "-decryption_key",
                    media.key_hex,
                    "-i",
                    str(enc_path),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(tmp_output),
                ]
            )
            tmp_output.replace(output)
        except (RuntimeError, FileNotFoundError, OSError) as e:
            await safe_unlink(tmp_output)
            raise DownloadException(f"红果视频解密失败: {e}") from e
        except BaseException:
            # 取消(撤回/超时)也必须清掉半截临时产物
            await safe_unlink(tmp_output)
            raise
        finally:
            # 加密中间产物是一次性签名直链，解密成功与否都无需保留
            await safe_unlink(enc_path)
        return output

    # -- 结果组装 ------------------------------------------------------------

    async def _build_result(self, series_id: str, vid: str, share_page: dict | None, url: str):
        series: _Series | None = None
        if series_id:
            try:
                series = await self._fetch_series(series_id)
            except (IgnoreException, TipException):
                raise
            except ParseException as e:
                if not vid:
                    raise
                logger.warning(f"hongguo 剧集详情获取失败（继续按分享数据解析）: {e}")
        if series is None and share_page is not None:
            series = await self._series_from_share_page(share_page)
        if series is None:
            raise ParseException("红果分享缺少剧集信息（链接可能已失效）")

        chapter_list = series.vid_list
        target_vid = vid or (chapter_list[0] if chapter_list else "")
        if not target_vid:
            raise ParseException(f"红果短剧《{series.title}》没有可播放的分集")

        media = await self._resolve_media(target_vid, series.series_id or series_id)
        video_task = asyncio.create_task(self._download_and_decrypt(media))

        # 集数编号：网页/分享数据均无可靠分集序（vid_list 为推荐序而非剧集序，
        # App 端 video_detail 又不开放），不臆造集号，只标注总集数
        title = series.title
        text_parts = []
        if series.episode_cnt > 1:
            text_parts.append(f"全{series.episode_cnt}集")
        if series.category:
            text_parts.append(f"#{series.category}")
        if series.intro:
            text_parts.append(series.intro[:150])

        return self.result(
            title=title,
            text="\n".join(part for part in text_parts if part) or None,
            contents=[self.create_video_content(video_task, series.cover or None, media.duration)],
            url=url,
        )
