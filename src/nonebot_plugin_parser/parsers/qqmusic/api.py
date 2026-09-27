"""QQ音乐 SDK 适配层。

两条实现路径，对上层暴露的接口一致（``get_song_detail`` / ``get_play_url`` /
``get_lyric``，另加 ``search_songs`` 供点歌用）：

1. **App 协议直连**（优先）：可选的本地增强引擎，支持数字 song_id、
   加密容器解密与更高音质；未安装时静默跳过。
2. **内置实现**：基于主依赖 ``qqmusic-api-python``。

本模块只做三件事：

1. **定位并导入**增强引擎（可选，缺失时回退内置实现）
2. **持久化设备指纹与登录态**（QQ 服务端做设备一致性校验，不能每次请求都换指纹）
3. 把引擎返回的原始结构**转成本插件既有的扁平 dict**
"""

from __future__ import annotations

import os
import re
import sys
import json
import asyncio
import importlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any
from pathlib import Path

from nonebot import logger

from ...config import _data_dir
from ...download import DOWNLOADER

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from ..base import BaseParser

# --------------------------------------------------------------------------- #
# 增强引擎定位
# --------------------------------------------------------------------------- #
#: 环境变量：手动指定增强引擎的源码目录（可多路径，用 ``os.pathsep`` 分隔）
ENV_PROTOCOL_PATH = "PARSER_QQMUSIC_PROTOCOL_PATH"

#: 环境变量：设备指纹 / QIMEI 的存放文件名前缀
_DEVICE_FILE = "qqmusic_device.json"


def _candidate_paths() -> list[Path]:
    """列出增强引擎可能的源码目录（环境变量 / 常见同级布局）。"""
    out: list[Path] = []
    env = os.environ.get(ENV_PROTOCOL_PATH, "")
    for raw in env.split(os.pathsep):
        if raw.strip():
            out.append(Path(raw.strip()))

    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "qqmusic-protocol" / "src"
        if cand.is_dir() and cand not in out:
            out.append(cand)
        if (parent / "app" / "qqmusic-protocol" / "src").is_dir():
            nested = parent / "app" / "qqmusic-protocol" / "src"
            if nested not in out:
                out.append(nested)
    return out


def _load_protocol() -> Any:
    """导入增强引擎；不可用时静默返回 ``None``（回退内置实现）。"""
    try:
        return importlib.import_module("qqmusic_protocol")
    except ImportError:
        pass

    for src_dir in _candidate_paths():
        path_str = str(src_dir)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
        try:
            return importlib.import_module("qqmusic_protocol")
        except ImportError:
            continue
    return None


protocol = _load_protocol()

# 显式 Any：条件绑定不能让类型检查器推断成 Optional（成员访问全部报 Optional 错）
DeviceInfo: Any
QQMusicClient: Any
DeviceFactory: Any
_qp_api: Any

if protocol is not None:
    logger.debug("QQ音乐: 使用 App 协议直连")
    DeviceInfo = protocol.DeviceInfo
    QQMusicClient = protocol.QQMusicClient
    DeviceFactory = protocol.DeviceFactory
    _qp_api = protocol.api
else:
    DeviceInfo = None
    QQMusicClient = None
    DeviceFactory = None
    _qp_api = None


# --------------------------------------------------------------------------- #
# 设备指纹持久化
# --------------------------------------------------------------------------- #
# QQ 服务端会校验 udid / QIMEI36 / androidId 的一致性，**每次请求换指纹会被判为异常设备**。
# 所以指纹只生成一次并落盘，之后所有请求复用。
_DEVICE_PATH = _data_dir / _DEVICE_FILE


def _load_device() -> Any:
    """读取已保存的设备指纹；没有或损坏则新建并落盘。"""
    if _DEVICE_PATH.exists():
        try:
            data = json.loads(_DEVICE_PATH.read_text(encoding="utf-8"))
            dev = DeviceInfo.from_dict(data)
        except Exception as e:
            logger.warning(f"QQ音乐: 设备指纹读取失败({e!r})，重新生成")
        else:
            if dev.android_id and dev.boot_id:
                return dev
            logger.warning("QQ音乐: 已存的设备指纹字段不全，重新生成")

    dev = DeviceFactory.generate(protocol.DevicePresets.DEFAULT_KEY)
    _save_device(dev)
    return dev


def _save_device(dev: Any) -> None:
    """把设备指纹写盘（QIMEI 拿到后也会回写）。"""
    try:
        _DEVICE_PATH.write_text(
            json.dumps(dev.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"QQ音乐: 设备指纹写入失败 {e!r}")


#: 进程内共享的设备指纹（仅增强引擎模式）
_DEVICE: Any = None
if protocol is not None:
    _DEVICE = _load_device()

#: 进程内共享的协议客户端（绑定设备指纹 + 共享 httpx 连接池）
_CLIENT: Any = None
_CLIENT_TASK: Any = None
_QIMEI_TRIED = False


def _get_client() -> Any:
    """取共享客户端；不存在则在当前事件循环里建一个（仅增强引擎模式）。"""
    global _CLIENT, _CLIENT_TASK

    if _CLIENT is not None:
        return _CLIENT
    if QQMusicClient is None:
        return None

    import asyncio

    from ...config import pconfig

    # httpx 的 AsyncClient 绑定事件循环；nonebot 换 loop 后必须重建，
    # 否则会拿到 "Event loop is closed"。
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _CLIENT_TASK is not None and _CLIENT_TASK is not loop:
        _CLIENT = None
    if _CLIENT is None:
        kwargs: dict[str, Any] = {"timeout": 45.0, "follow_redirects": True}
        proxy = getattr(pconfig, "parser_proxy", None)
        if proxy:
            kwargs["proxy"] = proxy
        try:
            import httpx

            _CLIENT = QQMusicClient(
                device=_DEVICE,
                credential=_current_credential(),
                http=httpx.AsyncClient(**kwargs),
            )
            _CLIENT_TASK = loop
        except Exception as e:
            logger.warning(f"QQ音乐: 客户端初始化失败 {e!r}")
            return None
    return _CLIENT


def _current_credential() -> Any:
    """取当前登录态（没有或过期则匿名）。"""
    from . import credential as cred_store

    return cred_store.load_credential_raw()


async def _ensure_qimei(client: Any) -> None:
    """首次使用时申请 QIMEI 并回写设备指纹。

    搜索在无 QIMEI 时会静默返回 0 条（不报错），所以这里必须主动补。
    """
    global _QIMEI_TRIED

    if _QIMEI_TRIED or _DEVICE.has_qimei:
        return
    _QIMEI_TRIED = True
    try:
        q = await client.ensure_qimei()
        _save_device(_DEVICE)
        logger.info(f"QQ音乐: QIMEI 申请成功 {q.q36[:12]}…")
    except Exception as e:
        logger.warning(f"QQ音乐: QIMEI 申请失败（搜索可能返回 0 条）: {e!r}")


# --------------------------------------------------------------------------- #
# 内置实现（基于主依赖 qqmusic-api-python；增强引擎不可用时的回退路径）
# --------------------------------------------------------------------------- #
_BUILTIN_URL_RETRY = 3
_BUILTIN_URL_RETRY_INTERVAL = 1.0


async def _builtin_song_detail(song_mid: str) -> dict:
    """内置详情：Web 平台 ``get_detail``，无需登录态。"""
    from qqmusic_api import Client

    async with Client() as client:
        resp = await client.song.get_detail(song_mid)
    track = resp.track
    if track is None:
        return {}
    singers = [s.name for s in (track.singer or []) if s.name]
    return {
        "name": track.name or track.title or "",
        "artist": " / ".join(singers) or "未知歌手",
        "pic_url": track.cover_url(500) or "",
        "duration": float(track.interval or 0),
        "song_mid": track.mid or song_mid,
        "media_mid": (track.file.media_mid if track.file else "") or "",
    }


async def _builtin_play_url(song_mid: str, media_mid: str, credential: Any | None) -> str | None:
    """内置取链：MP3_320 → MP3_128 → ACC_96 回退，带限流重试。"""
    from qqmusic_api import Client
    from qqmusic_api.modules.song import SongFileInfo, SongFileType

    for _ in range(_BUILTIN_URL_RETRY):
        async with Client(credential=credential) as client:
            cdn_dispatch = await client.song.get_cdn_dispatch()
            cdn = cdn_dispatch.sip[0] if cdn_dispatch.sip else ""
            if not cdn:
                logger.warning("QQ音乐: 无可用 CDN")
                return None
            # 每个音质单独 try：命中可放音质即返回，全部失败/异常则回退到下一音质。
            for file_type in (SongFileType.MP3_320, SongFileType.MP3_128, SongFileType.ACC_96):
                try:
                    urls = await client.song.get_song_urls(
                        [SongFileInfo(mid=song_mid, media_mid=media_mid, file_type=file_type)],
                        credential=credential,
                    )
                except Exception as e:
                    logger.debug(f"QQ音乐: 取链 {file_type} 异常: {e!r}")
                    continue
                for item in urls.data:
                    if item.result == 0 and item.purl:
                        return f"{cdn}{item.purl}"
        # 本轮所有音质都未命中（限流/无权限），短暂退避后重试
        await asyncio.sleep(_BUILTIN_URL_RETRY_INTERVAL)
    return None


async def _builtin_lyric(song_mid: str) -> str:
    """内置歌词：LRC 文本，失败返回空串。"""
    from qqmusic_api import Client

    try:
        async with Client() as client:
            resp = await client.lyric.get_lyric(song_mid)
            return resp.lyric or ""
    except Exception as e:
        logger.warning(f"QQ音乐歌词获取失败: {e!r}")
        return ""


async def _builtin_search_songs(keyword: str, limit: int) -> list[dict]:
    """内置搜索：``general_search``，字段映射对齐协议路径的返回 dict。"""
    from qqmusic_api import Client

    try:
        async with Client() as client:
            resp = await client.search.general_search(keyword, page=1, num=limit)
        # num 参数实测不生效(固定返回 30 条)，显式截断
        items = (resp.song.items if resp.song else [])[:limit]
    except Exception as e:
        logger.debug(f"QQ 音乐搜索失败,静默跳过: {e!r}")
        return []

    out: list[dict] = []
    for it in items:
        try:
            singers = [s.name for s in (it.singer or []) if s.name]
            # cover_url 是方法，需调用取值；album 可能为 None
            pic_url = ""
            if it.album and it.album.cover_url:
                cover = it.album.cover_url
                pic_url = cover() if callable(cover) else str(cover)
            out.append(
                {
                    "mid": it.mid or "",
                    "media_mid": (it.file.media_mid if getattr(it, "file", None) else "") or "",
                    "name": it.name or it.title or "未知歌曲",
                    "artist": " / ".join(singers) or "未知歌手",
                    "duration": float(it.interval or 0),
                    "pic_url": pic_url,
                    "is_paid": bool(getattr(it, "pay", None) and it.pay.pay_play == 1),
                }
            )
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
async def get_song_detail(parser: "BaseParser", song_mid: str) -> dict:
    """获取歌曲详情：标题、歌手、封面URL、时长（秒）、``media_mid``。

    找不到歌曲返回空 dict。
    """
    want = song_mid.strip()
    if protocol is None:
        if not want:
            return {}
        try:
            return await _builtin_song_detail(want)
        except Exception as e:
            # 查不到(404)/接口异常统一返回 {}，与引擎路径契约一致
            logger.debug(f"QQ音乐: 详情接口失败 {e!r}")
            return {}

    client = _get_client()
    if client is None or not want:
        return {}

    song = None
    if want.isdigit():
        # ★ 纯数字是 song_id 不是 mid（短链重定向常落数字形态 songDetail 页）。
        #   拿数字串当 mid 反查或当搜索词都查不到，必须走曲库 ids 反查。
        try:
            song = await _qp_api.track_by_id(client, int(want))
        except Exception as e:
            logger.debug(f"QQ音乐: ids 反查 {want} 失败: {e!r}")
    else:
        try:
            await _ensure_qimei(client)
            res = await _qp_api.search(client, want, limit=5)
            # ★ 搜索是**模糊**匹配，拿 mid 当关键词搜会带回一堆别的歌。
            #   必须精确挑出 mid 相同的那首，否则会解析成另一首歌。
            song = next((s for s in res.songs if s.mid == want), None)
            if song is None and res.songs and res.songs[0].mid == want:
                song = res.songs[0]
        except Exception as e:
            logger.debug(f"QQ音乐: 搜索定位 {want} 失败，退化曲库反查: {e!r}")

        if song is None:
            song = await _qp_api.track_by_mid(client, want)

    if song is None or not song.mid:
        return {}

    # 封面：搜索结果里可能没有，用曲库补一次
    pic = song.vs_pic
    if not pic:
        try:
            detail = await _qp_api.track_by_mid(client, song.mid)
        except Exception:
            detail = None
        if detail is not None:
            song = detail
            pic = song.vs_pic
            if not song.interval:
                song.interval = detail.interval

    return {
        "name": song.name or song.title or "",
        "artist": song.artist,
        "pic_url": pic or "",
        "duration": song.duration,
        "song_mid": song.mid or song_mid,
        "media_mid": song.media_mid or "",
    }


async def _download_and_decrypt(url: str, ekey: str, ext_headers: dict[str, str] | None = None) -> Path:
    """下载加密音频容器，用 ekey 派生密钥解密成明文音频文件。

    QQ 音乐匿名/部分档位给的是**加密容器**（``O8M1….mgg`` 等，密文与明文等长）。
    直接把 URL 交发送端，NapCat 语音转换必炸（retcode=1200「语音转换失败」）——
    密文不是可播放音频，必须先解密。

    解密按**绝对偏移**可寻址，整文件一次性解即可（一首歌几 MB）。
    """
    enc_path = await DOWNLOADER.download_audio(url, ext_headers=ext_headers)
    # decrypt 是就地异或，必须用可变 bytearray（bytes 会 TypeError）
    cipher = bytearray(enc_path.read_bytes())
    dec = protocol.StreamDecryptor(protocol.derive_key(ekey))
    dec.decrypt(0, cipher)
    dec_path = enc_path.with_suffix(".ogg")
    dec_path.write_bytes(cipher)
    logger.debug(f"QQ音乐: 加密容器已解密 {enc_path.name}({len(cipher)}B) -> {dec_path.name}")
    return dec_path


async def get_play_url(
    parser: "BaseParser",
    song_mid: str,
    media_mid: str,
    credential: Any | None = None,
) -> "str | asyncio.Task[Path] | None":
    """获取真实音频地址。

    依次尝试 320k → 640k → 128k（增强引擎内置回退链），
    返回首个 ``result == 0`` 且有 ``purl`` 的 ``{cdn}{purl}``。

    ★ 拼 CDN 文件名用的是 **media_mid**，不是 song_mid —— 用错会回
      ``result=104005``（容器名不对）。

    ★ 加密容器（带 ekey）返回的是**下载并解密到本地文件的 Task[Path]**，
      不是 URL —— ``create_audio_content`` 原生支持；明文容器（``M800….mp3``
      等按设计不返回 ekey）保持直链直通。

    Args:
        parser: 用于取下载 headers；可为 None（仅冒烟测试场景）。
        song_mid: 歌曲 mid。
        media_mid: 曲库给的 media_mid。
        credential: 保留参数；登录态统一由 :mod:`.credential` 管理。

    Returns:
        明文直链 URL、解密后本地文件的 Task[Path]，或全档失败时的 ``None``。
    """
    if protocol is None:
        return await _builtin_play_url(song_mid, media_mid, credential) if media_mid else None

    client = _get_client()
    if client is None or not media_mid:
        return None

    try:
        hosts = await client.cdn_hosts()
    except Exception as e:
        logger.debug(f"QQ音乐: 取 CDN 域名失败 {e!r}")
        hosts = []
    # CDN 调度常混回 IPv6 字面量地址（[240e:..]），而 DC/家宽 v6 路由经常
    # 不通（连接被静默断开且重试无意义）——优先挑普通域名/IP，全不行才兜底
    if ipv6 := [h for h in hosts if h.startswith("http://[") or h.startswith("https://[")]:
        hosts = [h for h in hosts if h not in ipv6] or ipv6
        logger.debug(f"QQ音乐: 跳过 IPv6 CDN {len(ipv6)} 个")

    got = await _qp_api.resolve_url_best(client, song_mid, media_mid, 320)
    if got is None:
        logger.debug(f"QQ音乐: {song_mid} 全部音质取链失败（无权限/付费/限流）")
        return None
    info, quality = got
    if not info.purl:
        logger.debug(f"QQ音乐: {song_mid} {quality.name} 无 purl")
        return None

    # purl 是相对路径，要拼 CDN 域名；服务端有时会直接回绝对地址
    if info.purl.startswith(("http://", "https://")):
        url = info.purl
    else:
        url = next((host + info.purl for host in hosts if host.endswith("/")), None)
        if url is None:
            return None

    if not info.ekey:
        # 明文容器（M800….mp3 等）按设计不返回 ekey，直通不加密
        return url

    # 加密容器：下载后用 ekey 解密成本地明文文件再发
    headers = getattr(parser, "headers", None)
    return asyncio.create_task(_download_and_decrypt(url, info.ekey, headers))


async def get_lyric(parser: "BaseParser", song_mid: str) -> str:
    """获取歌词文本（LRC）。无歌词或失败返回空串。"""
    if protocol is None:
        return await _builtin_lyric(song_mid) if song_mid else ""

    client = _get_client()
    if client is None or not song_mid:
        return ""

    try:
        detail = await _qp_api.track_by_mid(client, song_mid)
        song_id = detail.song_id if detail else 0
        doc = await _qp_api.lyric(client, song_mid, song_id)
    except Exception as e:
        logger.warning(f"QQ音乐歌词获取失败: {e!r}")
        return ""

    qrc = doc.get("lyric") or ""
    if not qrc:
        return ""
    try:
        return protocol_qrc_to_lrc(qrc)
    except Exception as e:
        logger.debug(f"QQ音乐: QRC→LRC 转换失败，回退原文: {e!r}")
        return qrc


async def search_songs(keyword: str, limit: int = 5) -> list[dict]:
    """搜索歌曲（供点歌使用）。

    Args:
        keyword: 搜索词。
        limit: 最多返回几首。

    Returns:
        ``[{"mid","media_mid","name","artist","duration","pic_url","is_paid"}]``；
        失败返回空列表（不抛异常 —— 点歌是三服务并发，单个服务挂了不能影响其他）。
    """
    if not keyword.strip():
        return []
    if protocol is None:
        return await _builtin_search_songs(keyword.strip(), limit)

    client = _get_client()
    if client is None:
        return []

    try:
        await _ensure_qimei(client)
        res = await _qp_api.search(client, keyword, limit=limit)
    except Exception as e:
        logger.debug(f"QQ 音乐搜索失败,静默跳过: {e!r}")
        return []

    out: list[dict] = []
    for s in res.songs[:limit]:
        out.append(
            {
                "mid": s.mid,
                "media_mid": s.media_mid,
                "name": s.name or s.title or "未知歌曲",
                "artist": s.artist,
                "duration": s.duration,
                "pic_url": s.vs_pic or "",
                "is_paid": s.is_paid,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# QRC → LRC
# --------------------------------------------------------------------------- #
#: 逐字时间戳 ``(相对毫秒,持续毫秒)``，跟在每个字符**后面**
_QRC_CHAR_TIMING = re.compile(r"\(\d+,\d+\)")

#: 行级时间戳 ``[行起始毫秒,行持续毫秒]``
_QRC_LINE_TIMING = re.compile(r"^\[(\d+),\d+\](.*)$")

#: LRC 元信息行 ``[ti:...]`` / ``[ar:...]`` / ``[offset:...]``
_LRC_META = re.compile(r"^\[[a-zA-Z]+:")

#: QRC 的逐字内容在 ``<Lyric_N LyricContent="...">`` 里（注意不是 ``<Lyric>``）
_QRC_CONTENT_ATTR = re.compile(r'LyricContent="([^"]*)"')


def protocol_qrc_to_lrc(qrc_text: str) -> str:
    """把 QRC（逐字 XML）转成普通 LRC。

    实测的真实结构（**不是**"``[起始毫秒]文字``"那种简化格式）::

        <?xml version="1.0" encoding="utf-8"?>
        <QrcInfos>
        <QrcHeadInfo SaveTime="269" Version="100"/>
        <LyricInfo LyricCount="1">
        <Lyric_1 LyricType="1" LyricContent="[ti:晴天]
        [ar:周杰伦]
        [al:叶惠美]
        [offset:0]
        [0,2250]天(0,160)天(160,160) (320,160)...
        [2250,2250]故(2250,450)事(2700,450)...
        ">
        </LyricInfo>
        </QrcInfos>

    两层时间戳：

    - ``[行起始毫秒,行持续毫秒]`` —— 行级，就是 LRC 要的那个
    - ``(相对毫秒,持续毫秒)`` —— 逐字，**相对行首**，跟在每个字符后面

    渲染层只认 LRC 行级格式，所以这里丢掉逐字层、保留行级层。
    ``[ti:]``/``[ar:]`` 等元信息是标准 LRC 头，原样保留。
    """
    if not qrc_text or not qrc_text.strip():
        return ""

    contents = _qrc_extract_contents(qrc_text)
    out: list[str] = []
    for content in contents:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if _LRC_META.match(line):
                out.append(line)
                continue
            m = _QRC_LINE_TIMING.match(line)
            if not m:
                continue
            begin_ms = int(m.group(1))
            # 去掉逐字时间戳，剩下就是这一行的纯文本
            text = _QRC_CHAR_TIMING.sub("", m.group(2))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                out.append(f"[{_ms_to_lrc(begin_ms)}]{text}")
    return "\n".join(out)


def _qrc_extract_contents(qrc_text: str) -> list[str]:
    """从 QRC 文本里取出所有 ``LyricContent`` 的值。

    先走 XML（能正确处理 ``&amp;`` 等实体转义），失败再退化到正则。
    """
    text = qrc_text.strip()
    # 服务端偶尔在 XML 前后塞空白/杂字符
    start = text.find("<?xml")
    if start < 0:
        start = text.find("<QrcInfos")
    if start > 0:
        text = text[start:]

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return _QRC_CONTENT_ATTR.findall(text)

    contents: list[str] = []
    for node in root.iter():
        # 元素名形如 Lyric_1 / Lyric_2；LyricType=0 是翻译、1 是主歌词
        if not node.tag.startswith("Lyric_"):
            continue
        content = node.get("LyricContent")
        if not content:
            continue
        # 只取主歌词（LyricType=1）；翻译/罗马音另有字段
        if node.get("LyricType") not in (None, "", "1"):
            continue
        contents.append(content)
    return contents


def _ms_to_lrc(ms: int) -> str:
    """毫秒 → ``mm:ss.xx``。"""
    ms = max(int(ms), 0)
    m, rem = divmod(ms, 60_000)
    s, cs = divmod(rem, 1000)
    return f"{m:02d}:{s:02d}.{cs // 10:02d}"


def reset_client() -> None:
    """丢弃共享客户端（换设备指纹/登录态后调用；测试用）。"""
    global _CLIENT, _CLIENT_TASK, _QIMEI_TRIED

    _CLIENT = None
    _CLIENT_TASK = None
    _QIMEI_TRIED = False


def current_device() -> Any:
    """当前共享设备指纹（``parqq设备`` 类指令展示用）。"""
    return _DEVICE
