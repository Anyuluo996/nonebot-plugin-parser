"""异步解析 API 的媒体转存与配额管理。

职责：
- 把解析产物（下载的媒体 / 渲染图）从插件缓存目录转存到 ``data_dir/api_media/{task_id}/``
  供 HTTP 端点流式分发；转存优先硬链接（同盘零拷贝），失败回退复制。
- 进程内配额计数器（预留-提交-回滚），控制转存目录总量不写满磁盘。
  释放点覆盖全部删除路径：DELETE 取消 / TTL 清理 / 孤儿目录 rmtree / 转存失败回滚；
  进程重启时从目录实际大小重建。

安全约定：
- task_id 与转存文件名均为 ``secrets.token_urlsafe`` 生成的不可猜值；
- 扩展名走白名单（MIME 推断与协议端类型识别都需要真实扩展名），其余统一 ``.bin``；
- 目录删除只按 task_id 拼接（task_id 已过格式校验），不拼接任何用户输入的文件名。
"""

import os
import shutil
import asyncio
import secrets
from pathlib import Path

from ..config import pconfig

MEDIA_ROOT: Path = pconfig.data_dir / "api_media"
"""媒体转存根目录（每任务一个子目录，目录名即 task_id）。"""

# 扩展名白名单：MIME 推断 / NapCat 类型识别依赖真实扩展名，白名单外的统一 .bin
EXT_WHITELIST: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".mp3", ".m4a", ".wav", ".silk"}
)

# 白名单扩展名 → MIME（media 端点显式指定，不依赖猜解）
EXT_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".silk": "audio/silk",
    ".bin": "application/octet-stream",
}


class MediaQuotaExceeded(Exception):
    """媒体转存配额超限。"""


def pick_ext(source_name: str | None) -> str:
    """从源文件名推断白名单内扩展名，白名单外（或无扩展名）统一 .bin。"""
    if source_name:
        suffix = Path(source_name).suffix.lower()
        if suffix in EXT_WHITELIST:
            return suffix
    return ".bin"


def pick_ext_from_magic(data: bytes) -> str:
    """按字节魔数推断白名单内扩展名（base64 段无源文件名时的兜底），识别不出 .bin。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) > 11 and data[4:8] == b"ftyp":
        return ".mp4"
    if data[:3] == b"ID3" or (data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3" or data[:2] == b"\xff\xf2"):
        return ".mp3"
    if data[:4] in (b"RIFF",) and data[8:12] in (b"WAVE",):
        return ".wav"
    return ".bin"


def gen_filename(ext: str) -> str:
    """生成不可猜的转存文件名：``{token_urlsafe(16)}{ext}``。"""
    if ext not in EXT_WHITELIST and ext != ".bin":
        ext = ".bin"
    return f"{secrets.token_urlsafe(16)}{ext}"


def task_media_dir(task_id: str) -> Path:
    """任务的媒体转存目录（不主动创建，由首次转存时 mkdir）。"""
    return MEDIA_ROOT / task_id


def _dir_size(path: Path) -> int:
    """同步测量目录总字节数（目录不存在返回 0）。"""
    total = 0
    if not path.is_dir():
        return 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


class MediaQuota:
    """进程内配额计数器（预留-提交-回滚）。

    NoneBot 单进程 asyncio 下并发预留经 Lock 串行化，计数不可能超限；
    计数器是软状态 —— 真正的磁盘上限还靠 TTL 清理兜底（删除路径都会释放配额）。
    """

    def __init__(self) -> None:
        self._used = 0
        self._lock = asyncio.Lock()

    @property
    def used(self) -> int:
        return self._used

    @property
    def limit(self) -> int:
        return pconfig.api_media_quota_mb * 1024 * 1024

    def rebuild(self) -> int:
        """从转存目录实际大小重建计数（进程重启时调用）。"""
        self._used = _dir_size(MEDIA_ROOT)
        return self._used

    async def reserve(self, size: int) -> None:
        """预留配额，超限抛 :class:`MediaQuotaExceeded`。"""
        if size <= 0:
            return
        async with self._lock:
            if self._used + size > self.limit:
                raise MediaQuotaExceeded(
                    f"media quota exceeded: {_used_mb(self._used)}+{_used_mb(size)} > {pconfig.api_media_quota_mb}MB"
                )
            self._used += size

    def settle(self, reserved: int, actual: int) -> None:
        """预留额与实际占用差额校正（base64 解码后实际字节数 ≠ 估算值）。"""
        self._used += actual - reserved

    def release(self, size: int) -> None:
        """释放配额（删除已转存文件 / 转存失败回滚时调用）。不为负（防重复释放）。"""
        if size <= 0:
            return
        self._used = max(0, self._used - size)


def _used_mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MB"


quota = MediaQuota()
"""模块级配额计数器单例。"""


def _link_or_copy(src: Path, dest: Path) -> None:
    """硬链接优先（同盘零拷贝），跨盘/不支持时回退复制。"""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.link(src, dest)  # Path.link_to 在 3.12+ 已移除，直接用 os.link
    except OSError:
        shutil.copy2(src, dest)


async def stage_path(src: Path, dest_dir: Path) -> tuple[str, int]:
    """转存一个本地文件，返回 ``(转存文件名, 字节数)``。

    内部完成预留 → 硬链/复制 → 失败回滚的完整闭环。转存文件名即时生成，
    与源文件名无关（不泄露宿主信息）。
    """

    def _stat() -> int:
        try:
            return src.stat().st_size
        except OSError as e:
            raise FileNotFoundError(f"staging source missing: {e!r}") from e

    size = await asyncio.to_thread(_stat)
    await quota.reserve(size)
    filename = gen_filename(pick_ext(src.name))
    dest = dest_dir / filename
    try:
        await asyncio.to_thread(_link_or_copy, src, dest)
    except Exception:
        quota.release(size)
        raise
    return filename, size


async def stage_bytes(data: bytes, dest_dir: Path, ext: str, *, reserved: int | None = None) -> tuple[str, int]:
    """把内存字节（渲染图切片等）落盘转存，返回 ``(转存文件名, 字节数)``。

    ``reserved``：调用方已在解码前按估算长度预留的额度（base64 路径），
    传入则本函数不再重复预留、写入后按实际字节数 settle 校正差额；
    None 则内部按 ``len(data)`` 预留。
    """
    if reserved is None:
        await quota.reserve(len(data))
    filename = gen_filename(ext)
    dest = dest_dir / filename
    try:
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)

        import aiofiles

        async with aiofiles.open(dest, "wb") as f:
            await f.write(data)
    except Exception:
        quota.release(reserved if reserved is not None else len(data))
        raise
    if reserved is not None:
        quota.settle(reserved, len(data))
    return filename, len(data)


async def remove_task_dir(task_id: str, known_bytes: int | None = None) -> None:
    """删除任务的媒体转存目录并释放配额。

    ``known_bytes`` 来自 DB staged_bytes（DB 与磁盘一致的正常路径）；传 None 时
    先实测目录大小再删（覆盖：取消时未写库的中途产物 / 孤儿目录 / 重复清理）。
    """
    dir_ = task_media_dir(task_id)

    def _do() -> int:
        if not dir_.is_dir():
            return 0
        size = _dir_size(dir_) if known_bytes is None else known_bytes
        shutil.rmtree(dir_, ignore_errors=True)
        return size

    freed = await asyncio.to_thread(_do)
    if freed:
        quota.release(freed)


def iter_task_dirs() -> list[Path]:
    """列出转存根目录下的全部任务目录（孤儿清理用）。"""
    if not MEDIA_ROOT.is_dir():
        return []
    return [p for p in MEDIA_ROOT.iterdir() if p.is_dir()]
