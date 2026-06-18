"""tdl (Telegram Downloader) 二进制封装。

tdl 是 iyear/tdl 提供的 Telegram 下载 CLI，通过子进程调用。
注意 tdl 不读取 http_proxy/https_proxy 环境变量，代理必须通过 --proxy 显式传入。
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING
from pathlib import Path
from dataclasses import dataclass

from nonebot import logger

from ..utils import generate_file_name
from ..config import pconfig
from ..exception import ParseException

if TYPE_CHECKING:
    from asyncio.subprocess import Process

# 单条消息导出的字段映射
_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi"})
_AUDIO_EXTS = frozenset({".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"})
_IMG_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"})


def is_tdl_available() -> bool:
    """检查 tdl 二进制是否可用（在 PATH 中或配置为绝对路径）。"""
    path = pconfig.tdl_path
    if not path:
        return False
    # 绝对路径直接判断存在性，否则在 PATH 中查找
    if Path(path).is_absolute():
        return Path(path).exists()
    return shutil.which(path) is not None


def _build_base_args() -> list[str]:
    """构建 tdl 公共参数：二进制路径、namespace、代理。"""
    args = [pconfig.tdl_path, "-n", pconfig.tdl_ns]
    if proxy := pconfig.tdl_proxy:
        args += ["--proxy", proxy]
    return args


async def _run(cmd: list[str], timeout: float = 300.0) -> tuple[int, str, str]:
    """异步运行命令，返回 (returncode, stdout, stderr)。"""
    import asyncio

    logger.debug(f"tdl run: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ParseException(f"未找到 tdl 二进制（{pconfig.tdl_path}），请安装 tdl 或配置 parser_tdl_path") from e

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ParseException(f"tdl 执行超时（{timeout}s）: {' '.join(cmd)}")
    return proc.returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


async def login_qr(timeout: float = 180.0) -> tuple[bool, str]:
    """执行 `tdl login --type qr` 扫码登录，返回 (是否登录成功, 提示信息/二维码ASCII)。

    这是 start_login_qr + wait_login_complete 的便捷封装，适合不需要中途
    发送二维码的场景。若需要在拿到二维码后立即发送给用户（避免二维码过期），
    请直接使用 start_login_qr / wait_login_complete 两阶段调用。
    """
    handle = await start_login_qr()
    if handle.error:
        return False, handle.error
    # 先把二维码返回（调用方据此前导渲染），但这里无法中途发送，故仅作等待
    success = await wait_login_complete(handle, timeout=timeout)
    if success:
        return True, "Telegram 登录成功"
    return False, handle.ascii_qr or "tdl 登录未完成（未捕获到二维码）"


@dataclass
class LoginQrHandle:
    """tdl login qr 的运行句柄（两阶段登录用）。"""

    proc: Process | None
    pgid: int | None
    ascii_qr: str
    """捕获到的 ASCII 二维码（start 阶段已填充）"""
    error: str
    """start 阶段的错误信息（若有则 ascii_qr 为空）"""
    _terminated: bool = False


async def start_login_qr(qr_wait_timeout: float = 30.0) -> LoginQrHandle:
    """启动 `tdl login --type qr` 并等待二维码输出。

    tdl 启动后会先打印 WARN + 'Scan QR code...' 然后输出 ASCII 二维码。
    本函数流式读取 stdout 直到捕获二维码（或超时/出错），然后返回句柄。
    进程仍在运行（等待用户扫码），调用方应：
    1. 用 handle.ascii_qr 渲染 PNG 发送给用户
    2. 调用 wait_login_complete(handle) 等待扫码完成

    Args:
        qr_wait_timeout: 等待二维码出现的最长时间（秒）

    Returns:
        LoginQrHandle: 若 error 非空表示启动失败；否则 ascii_qr 含二维码
    """
    import os
    import asyncio

    if not is_tdl_available():
        return LoginQrHandle(proc=None, pgid=None, ascii_qr="", error="tdl 不可用，无法执行登录")

    # 先清理可能残留的 tdl 进程（避免 bolt 数据库锁冲突）
    _kill_stale_tdl_processes()

    cmd = [*_build_base_args(), "login", "--type", "qr"]
    logger.info(f"tdl login qr 启动（等待二维码，最长 {qr_wait_timeout}s）")
    try:
        # start_new_session=True 让 tdl 独立成进程组，
        # 便于结束时用 os.killpg 杀整个组（含 go runtime 子进程），避免 bolt 锁残留
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return LoginQrHandle(
            proc=None,
            pgid=None,
            ascii_qr="",
            error=f"未找到 tdl 二进制（{pconfig.tdl_path}），请安装 tdl 或配置 parser_tdl_path",
        )

    assert proc.stdout is not None
    assert proc.stderr is not None

    pgid = os.getpgid(proc.pid) if proc.pid else None
    chunks: list[bytes] = []

    async def _read_until_qr_or_exit() -> str:
        """读取 stdout 直到捕获二维码或进程退出。返回提取的二维码（可能为空）。"""
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=2.0)
            except asyncio.TimeoutError:
                # 检查进程是否已退出（出错退出时 stdout 可能没更多数据）
                if proc.returncode is not None:
                    break
                continue
            if not chunk:
                break
            chunks.append(chunk)
            # 尝试提取二维码，拿到就停（不必读完全部输出）
            text = b"".join(chunks).decode(errors="replace")
            if extract_qr_ascii(text):
                return extract_qr_ascii(text)
        text = b"".join(chunks).decode(errors="replace")
        return extract_qr_ascii(text)

    try:
        qr = await asyncio.wait_for(_read_until_qr_or_exit(), timeout=qr_wait_timeout)
    except asyncio.TimeoutError:
        _terminate_process_group(proc, pgid)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        text = b"".join(chunks).decode(errors="replace")
        return LoginQrHandle(
            proc=None,
            pgid=pgid,
            ascii_qr=extract_qr_ascii(text),
            error="等待 tdl 二维码超时（请检查代理/网络）" if not extract_qr_ascii(text) else "",
        )

    # 进程可能已因错误退出（如锁冲突），检查
    if proc.returncode is not None and proc.returncode != 0:
        text = b"".join(chunks).decode(errors="replace")
        return LoginQrHandle(
            proc=None,
            pgid=pgid,
            ascii_qr=qr,
            error=f"tdl 启动后立即退出 (exit {proc.returncode}): {text.strip()[:200]}",
        )

    return LoginQrHandle(proc=proc, pgid=pgid, ascii_qr=qr, error="")


async def wait_login_complete(handle: LoginQrHandle, timeout: float = 120.0) -> bool:
    """等待 tdl login 进程结束（用户扫码确认后 exit 0）。

    Args:
        handle: start_login_qr 返回的句柄
        timeout: 等待扫码完成的最长时间（秒）

    Returns:
        bool: True 表示登录成功（exit 0）
    """
    import asyncio

    if handle.proc is None:
        return False

    try:
        await asyncio.wait_for(handle.proc.wait(), timeout=timeout)
        success = handle.proc.returncode == 0
    except asyncio.TimeoutError:
        success = False

    # 无论成功失败，确保进程组被彻底清理（避免 bolt 锁残留）
    _terminate_process_group(handle.proc, handle.pgid)
    try:
        await asyncio.wait_for(handle.proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass
    handle._terminated = True
    return success


def _terminate_process_group(proc: Process, pgid: int | None) -> None:
    """杀掉整个进程组（含 tdl 的 go runtime 子进程），避免 bolt 锁残留。"""
    import os

    if pgid is not None:
        try:
            os.killpg(pgid, 9)
        except ProcessLookupError:
            pass  # 进程组已退出
    else:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _kill_stale_tdl_processes() -> None:
    """清理可能残留的 tdl 进程，避免 bolt 数据库锁冲突。

    扫描 /proc（Linux）下所有进程，kill 掉 cmdline 含 tdl 的进程。
    Windows 无 /proc，跳过（Windows 下 tdl 无此锁问题）。
    """
    import os

    if os.name != "posix":
        return
    proc_dir = "/proc"
    if not os.path.isdir(proc_dir):
        return
    current_pid = os.getpid()
    for name in os.listdir(proc_dir):
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == current_pid:
            continue
        try:
            with open(f"{proc_dir}/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        # 匹配 tdl 二进制调用（排除当前 python 进程）
        if "tdl" in cmdline and "login" in cmdline:
            try:
                os.kill(pid, 9)
                logger.warning(f"清理残留 tdl 进程: PID={pid} cmd={cmdline[:80]}")
            except (ProcessLookupError, PermissionError):
                pass


def extract_qr_ascii(text: str) -> str:
    """从 tdl 的 stdout 中提取 ASCII 二维码块。

    tdl 输出含 'WARN' / 'Scan QR code...' 等前导行，二维码本身由
    [█▀▄ ] 四种字符构成。tdl 在 TTY 下会用 \\x1b[A 刷新二维码，
    非 TTY（管道捕获）时不会重绘，输出干净。这里按字符集过滤出二维码行，
    并取最后一次出现的连续块（防止刷新产生多份）。
    """
    qr_chars = {"█", "▀", "▄", " "}
    lines = text.splitlines()
    # 收集所有由二维码字符构成、且包含至少一个 block 字符的行
    qr_lines: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        # 去除 ANSI 转义序列（如 \x1b[A 光标上移）
        cleaned = _strip_ansi(line)
        if cleaned and all(c in qr_chars for c in cleaned) and ("█" in cleaned or "▀" in cleaned or "▄" in cleaned):
            current.append(cleaned)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    if not blocks:
        return ""
    # 取最后一个块（最新刷新的二维码）
    qr_lines = blocks[-1]
    return "\n".join(qr_lines)


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列（光标移动/颜色等）。"""
    import re

    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


async def fetch_messages(channel: str, message_id: int) -> dict:
    """通过 `tdl chat export` 获取频道/群组中指定消息的元数据。

    Args:
        channel: 频道用户名（如 anyul996）或数字 id
        message_id: 消息 id

    Returns:
        dict: 该消息的原始字段 {id, type, file, date, text?}
    """
    import tempfile

    if not is_tdl_available():
        raise ParseException("tdl 不可用，无法解析 Telegram 链接")

    with tempfile.TemporaryDirectory(prefix="tdl_export_") as tmpdir:
        out_file = Path(tmpdir) / "export.json"
        cmd = [
            *_build_base_args(),
            "chat",
            "export",
            "-c",
            str(channel),
            "-i",
            str(message_id),
            "--with-content",
            "-o",
            str(out_file),
        ]
        rc, _out, err = await _run(cmd)
        if rc != 0:
            raise ParseException(f"tdl chat export 失败 (exit {rc}): {err.strip()}")
        if not out_file.exists():
            raise ParseException("tdl chat export 未生成输出文件")
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ParseException(f"tdl chat export 输出解析失败: {e}") from e

    messages = data.get("messages", [])
    target = next((m for m in messages if m.get("id") == message_id), None)
    if target is None:
        raise ParseException(f"未在导出结果中找到消息 {message_id}")
    return target


async def download_media(
    url: str,
    *,
    dest_dir: Path,
    target_filename: str | None = None,
    timeout: float = 600.0,
) -> list[Path]:
    """通过 `tdl dl` 下载 Telegram 链接对应的媒体文件。

    使用 `--group` 自动聚合相册（grouped media）的所有媒体。
    下载到临时目录后，将文件重命名（按 url 哈希）并移动到 dest_dir。

    Args:
        url: 完整的 t.me 链接
        dest_dir: 最终存放目录（通常是 pconfig.cache_dir）
        target_filename: 若指定，用此文件名（不含扩展名）作为目标文件名前缀，
                         否则按 url 生成哈希文件名
        timeout: 子进程超时秒数

    Returns:
        list[Path]: 已移动到 dest_dir 的文件路径列表
    """
    import tempfile

    if not is_tdl_available():
        raise ParseException("tdl 不可用，无法下载 Telegram 媒体")

    dest_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tdl_dl_") as tmpdir:
        tmp_path = Path(tmpdir)
        cmd = [
            *_build_base_args(),
            "dl",
            "-u",
            url,
            "-d",
            str(tmp_path),
            "--group",
        ]
        rc, _out, err = await _run(cmd, timeout=timeout)
        if rc != 0:
            raise ParseException(f"tdl dl 失败 (exit {rc}): {err.strip()}")

        # 收集下载的文件
        downloaded = sorted(p for p in tmp_path.rglob("*") if p.is_file())
        if not downloaded:
            raise ParseException("tdl dl 完成但未下载到任何文件")

        moved: list[Path] = []
        for src in downloaded:
            ext = src.suffix or ""
            src_size = src.stat().st_size
            if target_filename:
                base = target_filename
                dest = dest_dir / f"{base}{ext}"
                # 同名且同大小视为已缓存，直接复用（避免重复下载同一链接）
                if dest.exists() and dest.stat().st_size == src_size:
                    moved.append(dest)
                    continue
                # 同名但大小不同（罕见，如同一链接内容变化），追加序号
                idx = 1
                while dest.exists():
                    dest = dest_dir / f"{base}_{idx}{ext}"
                    idx += 1
            else:
                # 复用 generate_file_name 生成稳定的哈希文件名（去重）
                fname = generate_file_name(src.name, ext)
                dest = dest_dir / fname
                if dest.exists() and dest.stat().st_size == src_size:
                    moved.append(dest)
                    continue
                idx = 1
                while dest.exists():
                    dest = dest_dir / f"{src.stem}_{idx}{ext}"
                    idx += 1

            import asyncio

            # 用 shutil.move 而非 Path.replace：临时目录和目标目录可能不在同一磁盘
            # （如 C: 的 tmp -> D: 的 cache），os.replace 在跨盘时会抛 WinError 17
            await asyncio.to_thread(shutil.move, str(src), str(dest))
            moved.append(dest)
        return moved


def classify_by_ext(filename: str) -> str:
    """根据文件扩展名分类媒体类型。

    Returns:
        "video" | "audio" | "image" | "file"
    """
    ext = Path(filename).suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _IMG_EXTS:
        return "image"
    return "file"
