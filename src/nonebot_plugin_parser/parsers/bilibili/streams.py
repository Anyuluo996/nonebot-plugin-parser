"""B站视频流选择：P2P 节点回避 + detect_best_streams 上游 bug 兜底。

从 ``parsers/bilibili/__init__.py`` 拆出，职责：
- 主链命中 P2P 边缘节点(mcdn 等)时从 backup_url 提取正规 CDN 替换
- bilibili_api ``detect_best_streams`` 因 codecs=None 排序崩溃时
  (上游 issue #1035)从 dash 原始数据降级重选
"""

from typing import Final
from urllib.parse import urlparse

from msgspec import MsgspecError, convert
from nonebot import logger
from bilibili_api.video import VideoQuality

from ...config import pconfig
from ...exception import ParseException

# B站 P2P 边缘节点 host 特征。实测这些节点(os 已被伪装成 bcache, 只能靠 host 判别):
#   - 速度仅 1-2M/s, 而正规 CDN(bilivideo.com)可达 5-25M/s
#   - 频繁连接重置(Connection reset), 是 "下载异常重试" 日志的主要来源
# backup_url 里必然存在正规 CDN, 主链命中 P2P 时优先换正规 CDN
_P2P_HOST_MARKERS: Final[tuple[str, ...]] = (
    "mcdn.bilivideo",  # xy116x196x156x92xy.mcdn.bilivideo.cn 等
    "edge.mountaintoys",  # *.edge.mountaintoys.cn (mcdn 专属域名, 4483 端口)
)


def _is_p2p_node(url: str) -> bool:
    """判断 URL 是否指向 P2P 边缘节点(mcdn 等), 这些节点质量差应优先回避"""
    host = urlparse(url).hostname or ""
    return any(marker in host for marker in _P2P_HOST_MARKERS)


def _select_preferred_streams(primary: str, backups: list[str]) -> tuple[str, list[str]]:
    """主链命中 P2P 节点时, 从 backup_url 里取第一个正规 CDN 提到主链位置。

    返回 (优选主链, 完整备用列表)。备用列表保留全部链接(含原 P2P 主链)供下载层重试轮换。
    全是 P2P 时保持主链(有总比没有强)。结果对 primary 去重(避免 B站偶发返回重复链接)。
    """
    if not _is_p2p_node(primary):
        return primary, _dedup_backups(backups, primary)
    for i, bu in enumerate(backups):
        if not _is_p2p_node(bu):
            # 把第一个正规 CDN 提到主链, 原 P2P 主链降级到 backup 首位
            new_backups = [primary, *backups[:i], *backups[i + 1 :]]
            logger.debug(f"主链命中 P2P 节点, 切换到正规 CDN: {_short_url(primary)} → {_short_url(bu)}")
            return bu, _dedup_backups(new_backups, bu)
    logger.warning(f"主链及所有 backup 均为 P2P 节点, 保持主链: {_short_url(primary)}")
    return primary, _dedup_backups(backups, primary)


def _dedup_backups(backups: list[str], primary: str) -> list[str]:
    """去重并排除与主链相同的链接, 保持原顺序"""
    seen: set[str] = {primary}
    out: list[str] = []
    for u in backups:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _short_url(url: str, n: int = 60) -> str:
    """日志友好的 URL 截断"""
    return url[:n] + ("..." if len(url) > n else "")


def safe_convert(raw, target_type, *, context: str):
    """安全 msgspec 转换：API 返回结构变化时抛 ParseException 而非 ValidationError 崩溃。

    B 站 API 在风控/改版中字段经常变动，msgspec.convert 缺字段/类型不符会抛
    ValidationError，未捕获会让整条解析直接失败。
    """
    try:
        return convert(raw, target_type)
    except MsgspecError as e:
        logger.warning(f"B站接口数据结构异常（{context}）: {e}")
        raise ParseException(f"B站接口数据解析失败（{context}）") from e


# B站 dash 视频流 codecs 字符串 → VideoCodecs 映射
# 上游 issue #1035: VideoCodecs.HEV.value="hev" 无法匹配 "hvc1.x.x"，导致 codecs=None
# 这里用自定义前缀映射兜底识别 hvc1/hev1 等变体
_CODEC_PREFIX_MAP: Final[dict[str, str]] = {
    "hvc1": "HEV",
    "hev1": "HEV",
    "hvc": "HEV",
    "avc1": "AVC",
    "avc": "AVC",
    "av01": "AV1",
    "av1": "AV1",
}


def _resolve_codecs(codecs_str: str):
    """根据 dash 返回的 codecs 字符串识别 VideoCodecs，识别不了返回 None"""
    from bilibili_api.video import VideoCodecs

    if not codecs_str:
        return None
    lower = codecs_str.lower()
    for prefix, name in _CODEC_PREFIX_MAP.items():
        if prefix in lower:
            return getattr(VideoCodecs, name, None)
    return None


def _fallback_select_streams(
    download_url_data: dict,
    *,
    max_quality: VideoQuality | int = 120,
    allowed_codecs: list | None = None,
) -> list:
    """bilibili_api detect_best_streams 降级: 直接从 dash 原始数据重新解析选最佳流

    绕开上游 issue #1035 中 detect() 把 hvc1 流的 video_codecs 置为 None 的问题：
    VideoStreamDownloadURL 构造后并未保留原始 codecs 字符串，所以这里从 dash dict
    重新提取，用 _resolve_codecs 自行识别编码。

    上游修复后(VideoCodecs.HEV.value 变成 tuple) detect_best_streams 不再抛异常，
    本方法不会被调用，自动成为 no-op。
    """
    from bilibili_api.video import (
        AudioQuality,
        AudioStreamDownloadURL,
        VideoStreamDownloadURL,
    )

    max_qv = max_quality.value if isinstance(max_quality, VideoQuality) else max_quality
    allowed = set(allowed_codecs) if allowed_codecs is not None else None

    video_streams: list[VideoStreamDownloadURL] = []
    audio_streams: list[AudioStreamDownloadURL] = []

    dash = download_url_data.get("dash") or {}
    # bangumi 数据可能多包一层 video_info
    if not dash and download_url_data.get("video_info"):
        dash = download_url_data["video_info"].get("dash") or {}

    # bilibili-api 17.4.2 起 VideoStreamDownloadURL / AudioStreamDownloadURL
    # 要求 backup_url / bandwidth / codecs / frame_rate / scale / sar /
    # mime_type / segment_base_* 等字段; 缺失键给安全默认值兼容老/裁剪 dash 数据。
    for vd in dash.get("video", []) or []:
        try:
            q = VideoQuality(vd["id"])
        except (KeyError, ValueError):
            continue
        # 忽略 HDR/杜比/超 max 的清晰度
        if q in (VideoQuality.HDR, VideoQuality.DOLBY):
            continue
        if q.value > max_qv:
            continue
        url = vd.get("baseUrl") or vd.get("base_url")
        if not url:
            continue
        codecs_enum = _resolve_codecs(vd.get("codecs", ""))
        if codecs_enum is None:
            # 识别不出编码的流直接丢弃，避免再次触发上游排序崩溃
            continue
        if allowed is not None and codecs_enum not in allowed:
            continue
        seg = vd.get("segment_base") or {}
        sar_raw = vd.get("sar", "1:1")
        try:
            parts = [int(x) for x in str(sar_raw).split(":")] if ":" in str(sar_raw) else [1, 1]
            # VideoStreamDownloadURL 要求 sar 为固定 2 元组 (width, height)
            sar = (parts[0], parts[-1]) if len(parts) >= 2 else (1, 1)
        except (TypeError, ValueError, IndexError):
            sar = (1, 1)
        try:
            frame_rate = float(vd.get("frame_rate", 0.0))
        except (TypeError, ValueError):
            frame_rate = 0.0
        video_streams.append(
            VideoStreamDownloadURL(
                url=url,
                video_quality=q,
                video_codecs=codecs_enum,
                backup_url=list(vd.get("backup_url", [])),
                bandwidth=int(vd.get("bandwidth", 0) or 0),
                codecs=str(vd.get("codecs", "")),
                frame_rate=frame_rate,
                scale=(vd.get("width", 0), vd.get("height", 0)),
                sar=sar,
                mime_type=str(vd.get("mime_type", "")),
                segment_base_initialization=str(seg.get("initialization", "")),
                segment_base_index_range=str(seg.get("index_range", "")),
            )
        )

    for ad in dash.get("audio", []) or []:
        try:
            q = AudioQuality(ad["id"])
        except (KeyError, ValueError):
            continue
        url = ad.get("baseUrl") or ad.get("base_url")
        if not url:
            continue
        if q.value > AudioQuality._192K.value:
            continue
        seg = ad.get("segment_base") or {}
        audio_streams.append(
            AudioStreamDownloadURL(
                url=url,
                audio_quality=q,
                backup_url=list(ad.get("backup_url", [])),
                bandwidth=int(ad.get("bandwidth", 0) or 0),
                codecs=str(ad.get("codecs", "")),
                mime_type=str(ad.get("mime_type", "")),
                segment_base_initialization=str(seg.get("initialization", "")),
                segment_base_index_range=str(seg.get("index_range", "")),
            )
        )

    best_video = max(video_streams, key=lambda s: s.video_quality.value, default=None)
    best_audio = max(audio_streams, key=lambda s: s.audio_quality.value, default=None)
    return [best_video, best_audio]


def detect_best_streams_safe(download_url_data: dict) -> list:
    """select best streams, detect_best_streams 崩溃时走 dash 原始数据降级。

    bilibili_api detect_best_streams 排序时 codecs=None 的流会触发 AttributeError
    (上游 issue #1035: hvc1/hev1 等编码无法匹配 VideoCodecs.value("hev")
    → video_codecs 残留 None → 排序崩溃)。
    """
    from bilibili_api.video import VideoDownloadURLDataDetecter

    detecter = VideoDownloadURLDataDetecter(download_url_data)
    try:
        return detecter.detect_best_streams(
            video_max_quality=pconfig.bili_video_quality,
            codecs=pconfig.bili_video_codes,
            no_dolby_video=True,
            no_hdr=True,
        )
    except AttributeError:
        logger.debug("detect_best_streams() failed (likely codecs=None), using fallback")
        return _fallback_select_streams(
            download_url_data,
            max_quality=pconfig.bili_video_quality,
            allowed_codecs=pconfig.bili_video_codes,
        )
