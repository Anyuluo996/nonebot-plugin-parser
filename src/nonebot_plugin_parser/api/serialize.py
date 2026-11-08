"""异步解析 API 的结果序列化：``ParseResult`` → 可存库载荷。

存库内容**零签名、零 base_url、零宿主路径**：
- 媒体（下载产物 / 渲染图 / 头像 / 封面）统一转存到 ``api_media/{task_id}/``，
  载荷中以 ``media://{filename}`` 占位符出现；
- base64 形态的段（渲染图切片走 ``Image(raw=bytes)`` 时）先按编码长度**预留配额**
  再解码落盘，避免大 payload 先吃内存；
- ``http(s)://`` 形态保留原样（本身已是公开 URL，无需转存）；
- base_url 拼接与 HMAC 现签在 routes 层响应时做（见 routes._resolve_media_urls），
  改 base_url / 轮换签名密钥都不会让存量任务失效。

段处理覆盖 OneBot 导出后的嵌套结构：合并转发 ``node`` 段的 ``data.content``
是段数组，走通用递归遍历，任何层级的 ``file`` 值都会被改写。
"""

import asyncio
import binascii
from typing import Any
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from nonebot import logger
from nonebot_plugin_alconna import SupportAdapter

from . import media
from ..helper import UniMessage
from ..parsers import (
    ParseResult,
    AudioContent,
    ImageContent,
    VideoContent,
    DynamicContent,
)
from ..renders import get_renderer

PLACEHOLDER_PREFIX = "media://"
"""载荷中的媒体占位符前缀（DB 与响应替换层的约定）。"""


def _placeholder(filename: str) -> str:
    return f"{PLACEHOLDER_PREFIX}{filename}"


class _Stager:
    """单任务的转存上下文：按源路径去重（同一文件在段与 metadata 间只转存一次）。

    ``degraded``：本次产物存在降级（某个媒体项转存失败被丢弃/标记 media_failed）。
    结果缓存以此判定「不完整成功」——降级产物不写入缓存（否则 24h 内同链接
    都会拿到缺媒体的结果）。
    """

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.staged: list[dict[str, Any]] = []
        self._bytes = 0
        self._by_source: dict[str, str] = {}
        self._dir = media.task_media_dir(task_id)
        self.degraded = False

    async def stage_path(self, src: Path) -> str:
        key = str(src)
        if key in self._by_source:
            return self._by_source[key]
        filename, size = await media.stage_path(src, self._dir)
        self.staged.append({"filename": filename, "size": size})
        self._bytes += size
        ph = _placeholder(filename)
        self._by_source[key] = ph
        return ph

    async def stage_bytes(self, data: bytes, ext: str, *, reserved: int | None = None) -> str:
        filename, size = await media.stage_bytes(data, self._dir, ext, reserved=reserved)
        self.staged.append({"filename": filename, "size": size})
        self._bytes += size
        return _placeholder(filename)

    async def stage_base64(self, encoded: str, ext: str) -> str:
        """base64 → 解码 → 落盘。先按编码长度估算预留配额，解码后 settle 校正。"""
        estimated = len(encoded) * 3 // 4
        await media.quota.reserve(estimated)
        try:
            data = await asyncio.to_thread(binascii.a2b_base64, encoded)
        except (binascii.Error, ValueError):
            media.quota.release(estimated)
            logger.warning(f"api 任务 {self.task_id[:8]}… base64 段解码失败，已丢弃该媒体")
            return ""
        return await self.stage_bytes(data, ext, reserved=estimated)

    @property
    def total_bytes(self) -> int:
        return self._bytes


# ── OneBot 导出段的媒体引用改写 ──────────────────────────────────────

_B64_PREFIX = "base64://"
_FILE_PREFIX = "file://"


async def _convert_file_ref(value: str, stager: _Stager) -> str:
    """把段 data 里的 ``file`` 值统一改写：转存 → 占位符；远程 URL 原样保留。

    任何转存失败/丢弃都置 ``stager.degraded``（结果缓存据此跳过降级产物）。
    """
    if value.startswith(_B64_PREFIX):
        encoded = value[len(_B64_PREFIX) :]
        # 解码前按魔数先窥探扩展名：只解头部避免全量解码后再判断
        try:
            head = binascii.a2b_base64(encoded[:32] + "==")
        except (binascii.Error, ValueError):
            head = b""
        placeholder = await stager.stage_base64(encoded, media.pick_ext_from_magic(head))
        if not placeholder:
            stager.degraded = True
        return placeholder

    if value.startswith(_FILE_PREFIX):
        path = Path(url2pathname(urlparse(value).path))
        if path.is_file():
            return await stager.stage_path(path)
        stager.degraded = True
        return ""

    if value.startswith(("http://", "https://")):
        return value

    # 少数导出形态会落地路径字符串（无 scheme）。存在才转存，避免把任意字符串误当路径
    path = Path(value)
    if path.is_file():
        return await stager.stage_path(path)
    logger.debug(f"api 任务 {stager.task_id[:8]}… 未知媒体引用形态，已丢弃: {value[:60]}")
    stager.degraded = True
    return ""


async def _convert_segments(obj: Any, stager: _Stager) -> Any:
    """递归遍历导出段结构，改写一切 ``file`` 键的媒体引用。

    OneBot 导出后的嵌套结构不止 dict/list：``node`` 段的 ``data.content`` 是
    ``Message``（list 子类，元素为 ``MessageSegment``），后者再展开为
    ``{"type","data"}`` 后继续走本函数；无法结构化的对象字符串化兜底，
    保证载荷整体 JSON 可序列化。
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "file" and isinstance(v, str) and v:
                out[k] = await _convert_file_ref(v, stager)
            else:
                out[k] = await _convert_segments(v, stager)
        return out
    if isinstance(obj, (list, tuple)):
        return [await _convert_segments(item, stager) for item in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # MessageSegment 等段对象：带 type/data 属性则按段展开，否则以不透明标记兜底
    # （不 str(obj)：其 repr 可能携带宿主路径等环境信息）
    seg_type, seg_data = getattr(obj, "type", None), getattr(obj, "data", None)
    if isinstance(seg_type, str) and isinstance(seg_data, dict):
        return await _convert_segments({"type": seg_type, "data": seg_data}, stager)
    return f"<opaque:{type(obj).__name__}>"


async def _message_to_segments(message: UniMessage[Any], stager: _Stager) -> list[dict[str, Any]]:
    """UniMessage → OneBot V11 段数组（file 引用已改写为占位符）。

    ``reply`` / ``at`` 段在导出后剥离：段里的 reply.id / at.qq 绑定的是**首次
    发送会话**的消息与触发者，进了结果缓存会在跨会话/跨机重放时回复错消息、
    @ 错人。当前渲染器本就不产这两种段，过滤是面向未来的写入侧不变量
    （读取侧重建再剔除一次，见 matchers/rcache_link）。
    """
    exported = await message.export(adapter=SupportAdapter.onebot11)
    raw = [{"type": seg.type, "data": dict(seg.data)} for seg in exported if seg.type not in ("reply", "at")]
    result: list[dict[str, Any]] = await _convert_segments(raw, stager)
    return result


# ── metadata 映射 ────────────────────────────────────────────────────


async def _meta_contents(result: ParseResult, stager: _Stager) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cont in result.contents:
        item: dict[str, Any]
        try:
            if isinstance(cont, VideoContent):
                item = {"type": "video"}
                if cont.duration:
                    item["duration"] = cont.duration
            elif isinstance(cont, AudioContent):
                item = {"type": "audio"}
                if cont.duration:
                    item["duration"] = cont.duration
            elif isinstance(cont, DynamicContent):
                item = {"type": "dynamic"}
            elif isinstance(cont, ImageContent):
                item = {"type": "image"}
                if cont.alt:
                    item["alt"] = cont.alt
            else:
                item = {"type": "unknown"}
            # 仅视频/动态有封面属性（isinstance 收窄供类型检查）；封面失败只丢封面，
            # 不拖垮本体（超时置 None 后抛 DownloadException 的口径与渲染层一致）
            if isinstance(cont, (VideoContent, DynamicContent)) and cont.cover is not None:
                try:
                    cover_path = await cont.get_cover_path()
                    if cover_path:
                        item["cover"] = await stager.stage_path(cover_path)
                except Exception as e:
                    logger.debug(f"api 任务 {stager.task_id[:8]}… 封面转存失败，丢弃封面: {e!r}")
            path = await cont.get_path()
            item["file"] = await stager.stage_path(path)
        except Exception as e:
            # 单项媒体失败不拖垮整个任务：标注失败继续
            logger.debug(f"api 任务 {stager.task_id[:8]}… 单项媒体转存失败: {e!r}")
            stager.degraded = True
            item = {"type": "media_failed"}
        out.append(item)
    return out


async def _meta_author(result: ParseResult, stager: _Stager) -> dict[str, Any] | None:
    if result.author is None:
        return None
    author: dict[str, Any] = {"name": result.author.name}
    if result.author.description:
        author["description"] = result.author.description
    if result.author.avatar is not None:
        try:
            avatar_path = await result.author.get_avatar_path()
            author["avatar"] = await stager.stage_path(avatar_path) if avatar_path else None
        except Exception as e:
            logger.debug(f"api 任务 {stager.task_id[:8]}… 头像转存失败: {e!r}")
            author["avatar"] = None
    return author


async def _metadata_of(result: ParseResult, stager: _Stager, *, top: bool) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "platform": result.platform.name,
        "platform_display": result.platform.display_name,
        "title": result.title,
        "text": result.text,
        "timestamp": result.timestamp,
        "url": result.url,
        "author": await _meta_author(result, stager),
    }
    if top:
        meta["contents"] = await _meta_contents(result, stager)
        graphics: list[dict[str, Any]] = []
        for gra in result.graphics:
            if isinstance(gra, str):
                graphics.append({"type": "text", "text": gra})
            else:
                try:
                    path = await gra.get_path()
                    graphics.append({"type": "image", "file": await stager.stage_path(path)})
                except Exception as e:
                    logger.debug(f"api 任务 {stager.task_id[:8]}… 图文项转存失败: {e!r}")
                    stager.degraded = True
        meta["graphics"] = graphics
    if result.repost is not None:
        meta["repost"] = await _metadata_of(result.repost, stager, top=False)
    return meta


# ── 入口 ─────────────────────────────────────────────────────────────


async def build_result(result: ParseResult, task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], int, bool]:
    """渲染 + 序列化 ``ParseResult``，返回 ``(载荷, staged清单, staged总字节, 是否降级)``。

    载荷结构::

        {
            "metadata": {...},                    # 结构化元数据（媒体为占位符）
            "messages": [ [段, ...], ... ],       # OneBot V11 段数组（可直接经协议端发送）
        }

    ``降级``：存在媒体项转存失败被丢弃——结果可用但不完整，结果缓存据此跳过。
    """
    # 全量等下载落盘（contents/render_image 的懒 Task → Path），后续转存只碰真实文件
    await result.ensure_downloads_complete()

    stager = _Stager(task_id)
    metadata = await _metadata_of(result, stager, top=True)

    renderer = get_renderer(result.platform.name)
    messages: list[list[dict[str, Any]]] = []
    async for message in renderer.render_messages(result):
        messages.append(await _message_to_segments(message, stager))

    payload = {"metadata": metadata, "messages": messages}
    return payload, stager.staged, stager.total_bytes, stager.degraded
