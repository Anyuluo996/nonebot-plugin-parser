"""聊天链路与跨机结果缓存的双向桥（二期）。

读路径 :func:`try_replay`：缓存命中后把 ``payload["messages"]``（一期序列化时
已完成渲染+长图切片+转发组装的 OneBot V11 段数组）重建为 UniMessage 列表，
交由 ``matchers._send_message_list`` 直发 —— 跳过解析与渲染，发出的内容与
首次完全一致。媒体占位符 ``media://`` 在重建前替换为本机
``api_media/{chat_task_id}/`` 的文件 URI（:func:`rcache.get` 已把文件物化到位）。

写路径 :func:`seed_from_result`：聊天解析成功后走与 API 链路同一套
``serialize.build_result`` 管线种缓存，本机/对端/API 三方共享。

重建策略（字节保真）：file 承载段（image/video/record/file）走 UniHelper 同款
构造（与首次发送同一路径，不依赖 ``UniMessage.of`` 的内部映射）；``text``/
``face`` 直构；未知/远程段 ``UniMessage.of`` 兜底（未映射段落 ``Other`` 携带
origin 导出透传）；``reply``/``at`` 段剥离 —— 二者绑定**首次发送会话**的
消息与触发者，跨会话/跨机重放会回复错消息、@ 错人。合并转发 node 重建时
``uid``/``name`` 本机化为当前 Bot，避免跨机重放出现陌生头像或触发风控。

门控与降级：任何环节失败一律返回 None / 静默跳过（回源解析），缓存永不影响
聊天主链路。缓存命中会把媒体物化进 ``api_media/{chat_task_id}/``，因此以
API 栈完整注册（``chat.is_recording_enabled``，即清理 job 存在）为前提 ——
``chat_task_id`` 为 None 即门控关闭。

导入约束（顺序敏感）：顶层**不得 import api 包** —— ``api.runner`` 顶层反向
依赖 ``matchers``，顶层导入成环；api.* 与 nonebot-adapter-onebot 一律函数级
懒导入。
"""

import asyncio
from typing import TYPE_CHECKING, Any
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from nonebot import logger
from nonebot.matcher import current_bot
from nonebot_plugin_alconna import SupportAdapter
from nonebot_plugin_alconna.uniseg import (
    Text,
    Emoji,
    Image,
    Video,
    Voice,
    Segment,
    Reference,
    CustomNode,
    UniMessage,
)

from ..config import pconfig
from ..helper import UniHelper

if TYPE_CHECKING:
    from ..parsers import ParseResult

_MEDIA_PREFIX = "media://"
"""载荷媒体占位符前缀（与 api.serialize.PLACEHOLDER_PREFIX 同一约定；
本地常量避免为读一个字符串懒导入 api 包）。"""

# P2-C：种缓存并发闸门（长图重切片 + zlib + Redis IO），忙时直接跳过不排队
_SEED_SEM = asyncio.Semaphore(2)


# ── 读路径：重放渲染产物 ─────────────────────────────────────────────


async def try_replay(raw_url: str, chat_task_id: str | None) -> list["UniMessage[Any]"] | None:
    """查询跨机缓存并重建消息列表；任何环节失败返回 None（调用方回源解析）。

    ``rcache.get`` 命中后媒体已物化到本任务目录并扣减配额 —— 此后步骤 5-7
    （占位符解析 / 重建 / staged 回写）任一失败都必须 ``remove_task_dir`` 清场，
    否则残留目录既占配额又会与回源后 seed 的转存混在同一 task 目录（P1-1）。
    """
    if not chat_task_id or not pconfig.rcache_enabled:
        return None
    # 仅 OneBot V11 适配器重放：缓存段数组是 onebot11 形态，其他适配器（TG 等）
    # 语义不兼容，直接回源解析。current_bot 在 handler 上下文内恒可用。
    try:
        bot = current_bot.get()
    except LookupError:
        return None
    if bot.adapter.get_name() != SupportAdapter.onebot11.value:
        return None

    from ..api import media, store, rcache
    from ..api.runner import normalize_loose_url

    cached = await rcache.get(normalize_loose_url(raw_url), chat_task_id)
    if cached is None:
        return None
    payload, staged, staged_bytes = cached

    task_dir = media.task_media_dir(chat_task_id)
    dir_resolved = task_dir.resolve()
    try:
        payload_messages = payload.get("messages")
        if not isinstance(payload_messages, list) or not payload_messages:
            raise ValueError("payload.messages 为空")
        staged_names = frozenset(f["filename"] for f in staged)
        if not _resolve_media_refs(payload_messages, staged_names, task_dir, dir_resolved):
            raise ValueError("媒体占位符解析失败（文件缺失或引用违规）")
        messages = [m for m in await _rebuild_messages(payload_messages) if len(m)]
        if not messages:
            raise ValueError("重建后无任何可发送段")
        # staged 回写（P1-2）：对端拉取本机种的条目时按 get_staged_files 反查
        # 媒体端点清单，缺了会 404。放最后一步：此前任一失败都不留 DB 残迹。
        await store.update_staged(chat_task_id, staged, staged_bytes)
    except Exception:
        await media.remove_task_dir(chat_task_id)
        logger.opt(exception=True).debug("结果缓存重放失败，已清场按未命中回源")
        return None
    return messages


def _resolve_media_refs(obj: Any, staged_names: frozenset[str], task_dir: Path, dir_resolved: Path) -> bool:
    """递归把 ``file`` 键的 ``media://`` 占位符替换为本机文件 URI（就地改写，
    payload 为本次解码的新对象）。任一占位符违规/文件缺失返回 False。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "file" and isinstance(v, str) and v.startswith(_MEDIA_PREFIX):
                resolved = _resolve_placeholder(v, staged_names, task_dir, dir_resolved)
                if resolved is None:
                    return False
                obj[k] = resolved
            elif not _resolve_media_refs(v, staged_names, task_dir, dir_resolved):
                return False
        return True
    if isinstance(obj, list):
        return all(_resolve_media_refs(item, staged_names, task_dir, dir_resolved) for item in obj)
    return True


def _resolve_placeholder(value: str, staged_names: frozenset[str], task_dir: Path, dir_resolved: Path) -> str | None:
    """单个占位符 → 本机文件 URI。三重校验（P2-A belt-and-braces）：

    1. 文件名 ∈ 条目 staged 清单（清单本身已过 _validate_entry 白名单）；
    2. ``resolve()`` 后仍在任务目录内（防穿越二次兜底）；
    3. 文件真实存在（源目录被清理/硬链失效 → 未命中回源）。
    """
    filename = value[len(_MEDIA_PREFIX) :]
    if filename not in staged_names:
        return None
    target = (task_dir / filename).resolve()
    if target.parent != dir_resolved or not target.is_file():
        return None
    return target.as_uri()


# ── 段重建（手工构造为主，字节保真） ─────────────────────────────────


async def _rebuild_messages(payload_messages: list[Any]) -> list["UniMessage[Any]"]:
    return [await _rebuild_one(seg_list) for seg_list in payload_messages if isinstance(seg_list, list)]


async def _rebuild_one(seg_list: list[Any]) -> "UniMessage[Any]":
    """单条消息重建。node 段（合并转发导出时被拍平为顶层多个）聚回单个
    ``Reference``；普通段按类型直构。"""
    nodes: list[CustomNode] = []
    segs: list[Segment] = []
    for seg in seg_list:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        data = seg.get("data")
        if not isinstance(seg_type, str) or not isinstance(data, dict):
            continue
        if seg_type == "node":
            node = await _rebuild_node(data)
            if node is not None:
                nodes.append(node)
            continue
        if seg_type in ("reply", "at"):
            continue  # P1-A：跨会话错位，重建侧再剔除一次（写入侧已滤）
        segs.extend(await _build_segments(seg_type, data))
    if nodes:
        return UniMessage([Reference(nodes=nodes), *segs])
    return UniMessage(segs)


async def _rebuild_node(data: dict[str, Any]) -> CustomNode | None:
    """合并转发节点重建：content 递归重建（内层同样剥离 reply/at）；
    ``uid``/``name`` 本机化（P1-3）—— B 机重放时节点归属当前 Bot，
    与 helper.construct_forward_message 的首次构造语义一致。"""
    content_data = data.get("content")
    if not isinstance(content_data, list):
        return None
    content_segs: list[Segment] = []
    for seg in content_data:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        seg_data = seg.get("data")
        if not isinstance(seg_type, str) or not isinstance(seg_data, dict):
            continue
        if seg_type in ("reply", "at"):
            continue
        content_segs.extend(await _build_segments(seg_type, seg_data))
    uid: Any = data.get("user_id")
    try:
        uid = current_bot.get().self_id
    except LookupError:
        uid = uid if uid is not None else "0"
    name = data.get("nickname")
    return CustomNode(
        uid=str(uid),
        name=name if isinstance(name, str) and name else pconfig.nickname,
        content=UniMessage(content_segs),
    )


async def _build_segments(seg_type: str, data: dict[str, Any]) -> list[Segment]:
    """单段重建，返回段列表（通常一项；未知/缺装形态返回空，不影响其余段）。"""
    if seg_type == "text":
        text = data.get("text")
        return [Text(text)] if isinstance(text, str) and text else []
    if seg_type == "face":
        face_id = data.get("id")
        return [Emoji(str(face_id))] if face_id is not None else []
    if seg_type in ("image", "video", "record"):
        return await _build_media(seg_type, data)
    if seg_type == "file":
        return await _build_file_seg(data)
    return await _of_fallback(seg_type, data)


async def _build_media(seg_type: str, data: dict[str, Any]) -> list[Segment]:
    """file 承载段：本地文件走 UniHelper 同款构造（按**本机** use_base64，
    与首次发送同一路径 → 导出字节保真）；远程公开 URL 原样回放；其余形态空。"""
    file_val = data.get("file")
    if not isinstance(file_val, str) or not file_val:
        return []
    if file_val.startswith("file://"):
        path = _local_path_of(file_val)
        if path is None:
            return []
        if seg_type == "image":
            return [await UniHelper.img_seg(path)]
        if seg_type == "record":
            return [await UniHelper.record_seg(path)]
        # video：payload 段类型已是首次发送的决策结果（超限转文件段的情形
        # 落在 "file" 分支），直接构造，不复检尺寸
        if pconfig.use_base64:
            return [Video(raw=await asyncio.to_thread(path.read_bytes))]
        return [Video(path=path)]
    if file_val.startswith(("http://", "https://")):
        # 写路径保留原样的公开远程引用，回放即原值
        if seg_type == "image":
            return [Image(url=file_val)]
        if seg_type == "video":
            return [Video(url=file_val)]
        if seg_type == "record":
            return [Voice(url=file_val)]
    return []


async def _build_file_seg(data: dict[str, Any]) -> list[Segment]:
    file_val = data.get("file")
    if not isinstance(file_val, str) or not file_val.startswith("file://"):
        return []
    path = _local_path_of(file_val)
    if path is None:
        return []
    name = data.get("name")
    display_name = name if isinstance(name, str) and name else None
    return [await UniHelper.file_seg(path, display_name=display_name)]


def _local_path_of(file_val: str) -> Path | None:
    """``file://`` URI → 本机存在的 Path；文件不在（NapCat 不可见/被清理）→ None。"""
    path = Path(url2pathname(urlparse(file_val).path))
    if not path.is_file():
        return None
    return path


async def _of_fallback(seg_type: str, data: dict[str, Any]) -> list[Segment]:
    """未知段类型兜底：onebot11 Message → ``UniMessage.of`` 重建（未映射段
    落 ``Other`` 携带 origin 导出透传，字节保真）。适配器缺装 → 空。"""
    try:
        from nonebot.adapters.onebot.v11 import Message as Ob11Message
        from nonebot.adapters.onebot.v11 import MessageSegment as Ob11Segment
    except ImportError:
        return []
    msg = Ob11Message([Ob11Segment(seg_type, dict(data))])
    return list(UniMessage.of(msg, adapter=SupportAdapter.onebot11.value))


# ── 写路径：聊天种缓存 ───────────────────────────────────────────────


async def seed_from_result(
    result: "ParseResult",
    raw_url: str,
    keyword: str,
    chat_task_id: str | None,
) -> None:
    """聊天解析成功后种跨机缓存。尽力而为：任一门控不过 / 异常静默返回。

    时序契约：由 parser_handler 在完成反馈（record_chat_result + done 表情）
    **之后**调用 —— build_result 的长图重切片+压缩+Redis IO 可达数百毫秒，
    不挡用户完成反馈（P2-3）。此时下载已全部落地（发送阶段消费过），
    render_image 已落盘，二次渲染直接复用，仅长图重切片 + 媒体硬链转存。

    skip 判定用**本次命中的 keyword** 与 API put 语义完全对齐（put 内部同样
    按 keyword 精确匹配兜底）。勿改成 parser 实例全量 keyword 判定——B 站直播
    （live.bili）与普通视频同属 BilibiliParser，全量判定会把整个平台的聊天
    seed 全部误杀（2026-11-08 生产实测教训）；b23 短链直指直播的场景两侧
    （API/聊天）行为一致地都会种缓存，一致性优先。
    """
    if not chat_task_id or not pconfig.rcache_enabled:
        return
    if keyword in pconfig.rcache_skip_keywords:
        return  # 本次命中的 keyword 是直播类（与 API put 同一判定口径）
    if _SEED_SEM.locked():
        return  # P2-C：忙时直接跳过，种缓存是尽力而为，不排队不拖慢 bot

    from ..api import media, store, rcache, serialize
    from ..api.runner import normalize_loose_url

    async with _SEED_SEM:
        try:
            payload, staged, staged_bytes, degraded = await serialize.build_result(result, chat_task_id)
        except Exception:
            # 半途异常可能已转存部分文件：清场防半拉目录白占配额（渲染期失败的
            # 媒体项走 media_failed→degraded 正常返回，不进此分支）
            await media.remove_task_dir(chat_task_id)
            logger.opt(exception=True).debug("种缓存序列化失败，已清场")
            return
        try:
            # staged 回写（P1-2）：对端经媒体端点拉取本机种的条目时按
            # get_staged_files 反查，缺了会 404
            await store.update_staged(chat_task_id, staged, staged_bytes)
        except Exception:
            await media.remove_task_dir(chat_task_id)
            logger.opt(exception=True).debug("种缓存 staged 回写失败，已清场")
            return
        try:
            await rcache.put(
                normalize_loose_url(raw_url), payload, staged, staged_bytes, chat_task_id, keyword, degraded=degraded
            )
        except Exception:
            # put 失败保留目录：行已带 staged 清单，TTL 清理按实测释放，账本平衡
            logger.opt(exception=True).debug("种缓存写入失败（目录保留，随行 TTL 清理）")
