"""tdl (Telegram Downloader) 二进制封装。

tdl 是 iyear/tdl 提供的 Telegram 下载 CLI，通过子进程调用。
注意 tdl 不读取 http_proxy/https_proxy 环境变量，代理必须通过 --proxy 显式传入。

登录功能（login_qr）基于 pty（伪终端）运行 tdl：
- tdl 的二维码渲染和 2FA 密码交互均依赖终端环境
- 账号开启两步验证时，扫码后 tdl 提示 'Enter 2FA Password:'
- 通过 pty stdin 写入密码完成登录
- 仅支持 Linux/macOS（Windows 无 pty/fork）
"""

from __future__ import annotations

import os
import re
import json
import time
import shutil
import asyncio
import threading
from pathlib import Path
from dataclasses import field, dataclass

from nonebot import logger

from ..utils import generate_file_name
from ..config import pconfig
from ..exception import ParseException


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列（光标移动/颜色等）。"""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


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
    success = await wait_login_complete(handle, timeout=timeout)
    if success:
        return True, "Telegram 登录成功"
    return False, handle.ascii_qr or "tdl 登录未完成（未捕获到二维码）"


@dataclass
class LoginQrHandle:
    """tdl login qr 的运行句柄（两阶段登录用）。

    基于 pty（伪终端）运行 tdl，以便支持 2FA 密码交互。
    账号开启两步验证时，扫码后 tdl 会提示 'Enter 2FA Password:'，
    需调用 submit_2fa_password(handle, password) 传入密码。
    """

    master_fd: int | None = None
    """pty master 文件描述符（用于读写 tdl 的 stdin/stdout）"""
    pid: int | None = None
    """tdl 子进程 pid"""
    pgid: int | None = None
    """tdl 进程组 id（用于 os.killpg 清理）"""
    ascii_qr: str = ""
    """捕获到的 ASCII 二维码（start 阶段已填充）"""
    error: str = ""
    """start 阶段的错误信息（若有则 ascii_qr 为空）"""
    _terminated: bool = False
    _reader_thread: object | None = None
    """后台读线程（持续把 pty 输出存到 _output_buffer 并处理 survey 光标回显）"""
    _output_buffer: bytearray = field(default_factory=bytearray)
    """tdl 累计输出（用于检测 2FA 提示、提取二维码、判断成功）"""


async def start_login_qr(qr_wait_timeout: float = 30.0) -> LoginQrHandle:
    """启动 `tdl login --type qr`（pty 模式）并等待二维码输出。

    tdl 启动后会先打印 WARN + 'Scan QR code...' 然后输出 ASCII 二维码。
    本函数用 pty 运行 tdl（tdl 的二维码渲染 + 2FA 交互均依赖终端环境），
    流式读取直到捕获二维码（或超时/出错），然后返回句柄。
    进程仍在运行（等待用户扫码），调用方应：
    1. 用 handle.ascii_qr 渲染 PNG 发送给用户
    2. 调用 wait_login_complete(handle) 等待扫码（会自动检测 2FA）

    Args:
        qr_wait_timeout: 等待二维码出现的最长时间（秒）

    Returns:
        LoginQrHandle: 若 error 非空表示启动失败；否则 ascii_qr 含二维码
    """
    if not is_tdl_available():
        return LoginQrHandle(error="tdl 不可用，无法执行登录")

    # 先清理可能残留的 tdl 进程（避免 bolt 数据库锁冲突）
    _kill_stale_tdl_processes()

    cmd = [*_build_base_args(), "login", "--type", "qr"]
    logger.info(f"tdl login qr 启动（pty 模式，等待二维码，最长 {qr_wait_timeout}s）")

    # pty 必须在同步代码里 fork/exec，用 to_thread 包一层
    try:
        result = await asyncio.to_thread(_pty_spawn, cmd)
    except FileNotFoundError:
        return LoginQrHandle(error=f"未找到 tdl 二进制（{pconfig.tdl_path}），请安装 tdl 或配置 parser_tdl_path")
    except OSError as e:
        return LoginQrHandle(error=f"启动 tdl 失败（pty 创建失败，仅支持 Linux/macOS）: {e}")

    master_fd, pid, pgid = result

    handle = LoginQrHandle(master_fd=master_fd, pid=pid, pgid=pgid)

    # 启动后台读线程：持续读 pty 输出，存入 buffer，并自动响应 survey 的光标回显请求
    reader = threading.Thread(target=_pty_reader_loop, args=(handle,), daemon=True)
    handle._reader_thread = reader
    reader.start()

    # 等待二维码出现（轮询 buffer）
    deadline = asyncio.get_event_loop().time() + qr_wait_timeout
    while asyncio.get_event_loop().time() < deadline:
        text = bytes(handle._output_buffer).decode(errors="replace")
        qr = extract_qr_ascii(text)
        if qr:
            handle.ascii_qr = qr
            return handle
        # 检查进程是否已退出（启动失败如锁冲突）
        if not _is_process_alive(pid):
            # 进程已退出，等待 reader 收尾
            reader.join(timeout=2)
            text = bytes(handle._output_buffer).decode(errors="replace")
            qr = extract_qr_ascii(text)
            handle.ascii_qr = qr
            if qr:
                return handle
            return LoginQrHandle(
                master_fd=None,
                pid=pid,
                pgid=pgid,
                ascii_qr=qr,
                error=f"tdl 启动后立即退出: {strip_ansi(text).strip()[:200]}",
            )
        await asyncio.sleep(0.5)

    # 超时
    text = bytes(handle._output_buffer).decode(errors="replace")
    qr = extract_qr_ascii(text)
    _terminate_handle(handle)
    return LoginQrHandle(
        pgid=pgid,
        ascii_qr=qr,
        error="等待 tdl 二维码超时（请检查代理/网络）" if not qr else "",
    )


async def wait_login_complete(handle: LoginQrHandle, timeout: float = 120.0) -> bool:
    """等待 tdl login 进程结束（用户扫码确认后 exit 0）。

    会自动检测 2FA 提示。若账号开启两步验证，扫码后 tdl 输出 'Enter 2FA Password:'，
    本函数检测到后返回 False 并设置 handle.error="2FA_REQUIRED"（调用方应提示用户输密码，
    再调用 submit_2fa_password(handle, pwd) + 再次调用本函数等待）。

    Args:
        handle: start_login_qr 返回的句柄
        timeout: 等待扫码完成的最长时间（秒）

    Returns:
        bool: True 表示登录成功（tdl exit 0）
    """
    if handle.pid is None or handle._terminated:
        return False

    deadline = asyncio.get_event_loop().time() + timeout
    poll_count = 0
    while asyncio.get_event_loop().time() < deadline:
        text = bytes(handle._output_buffer).decode(errors="replace")
        poll_count += 1
        # 每 2 秒打印一次 buffer 状态，便于排查（DEBUG 级别）
        if poll_count % 4 == 0:
            alive = _is_process_alive(handle.pid)
            buf_tail = strip_ansi(text).replace("\n", "|")[-120:]
            logger.debug(
                f"wait_login poll#{poll_count} alive={alive} "
                f"buf={len(handle._output_buffer)}B "
                f"has_2fa={'2FA Password' in text} "
                f"has_success={'Login successfully' in text} "
                f"tail=...{buf_tail}"
            )
        # 检测 2FA 提示（大小写不敏感）
        lower = text.lower()
        if "2fa password" in lower or "enter 2fa" in lower:
            logger.info("检测到 2FA 密码提示，等待用户输入密码")
            handle.error = "2FA_REQUIRED"
            return False
        # 检测成功
        if "login successfully" in lower:
            logger.info("检测到登录成功")
            # 等待进程自然退出
            await asyncio.to_thread(_wait_pid, handle.pid, 10)
            _terminate_handle(handle)
            return True
        # 检测进程退出
        if not _is_process_alive(handle.pid):
            text2 = bytes(handle._output_buffer).decode(errors="replace")
            success = "login successfully" in text2.lower()
            logger.debug(
                f"tdl 进程退出 alive=False success={success} tail=...{strip_ansi(text2).replace(chr(10), '|')[-150:]}"
            )
            _terminate_handle(handle)
            return success
        await asyncio.sleep(0.5)

    logger.debug("wait_login 超时退出")
    _terminate_handle(handle)
    return False


async def submit_2fa_password(handle: LoginQrHandle, password: str) -> None:
    """向 tdl 提交 2FA 密码（通过 pty stdin 写入）。

    在 wait_login_complete 检测到 2FA_REQUIRED 后调用。
    survey 库会先发 \\x1b[6n 请求光标位置（_pty_reader_loop 已自动应答），
    然后读取按键。本函数直接写入密码 + 换行。

    Args:
        handle: 已检测到 2FA 的句柄
        password: 两步验证密码（明文，仅用于本次写入，不持久化）
    """
    if handle.master_fd is None:
        raise ParseException("tdl 进程未运行，无法提交 2FA 密码")
    # 清除 2FA_REQUIRED 标记
    handle.error = ""
    await asyncio.to_thread(_write_pty, handle.master_fd, password + "\n")
    logger.info("已向 tdl 提交 2FA 密码")


# ============ pty 辅助函数（同步，由 to_thread 调用）============


def _pty_spawn(cmd: list[str]) -> tuple[int, int, int]:
    """在 pty 中启动 tdl，返回 (master_fd, pid, pgid)。"""
    import pty
    import fcntl
    import struct
    import termios

    master, slave = pty.openpty()
    # 设置窗口大小，让 tdl 二维码渲染正常
    winsize = struct.pack("HHHH", 50, 120, 0, 0)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)

    pid = os.fork()
    if pid == 0:
        # 子进程：attach 到 slave pty
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        if master > 2:
            os.close(master)
        os.execvp(cmd[0], cmd)
        os._exit(127)

    os.close(slave)
    pgid = os.getpgid(pid)
    # 设置 master 非阻塞，便于 reader loop

    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    return master, pid, pgid


def _pty_reader_loop(handle: LoginQrHandle) -> None:
    """后台线程：持续读 pty 输出存入 buffer，并响应 survey 的光标回显请求。

    survey 库在渲染 prompt 时会发 \\x1b[6n（DSR，请求光标位置），
    终端应回复 \\x1b[row;colR，否则 survey 会卡住等待。
    这里检测到 \\x1b[6n 就回复一个固定的光标位置 \\x1b[1;1R。
    """
    import errno

    master = handle.master_fd
    if master is None:
        return
    while not handle._terminated:
        try:
            data = os.read(master, 4096)
        except OSError as e:
            if e.errno == errno.EAGAIN:
                time.sleep(0.1)
                continue
            break  # EOF 或错误
        if not data:
            break
        handle._output_buffer.extend(data)
        # 检测关键内容时打印日志（2FA / 成功 / 光标请求）
        if b"2FA" in data or b"2fa" in data:
            logger.debug(f"pty reader 捕获 2FA 相关: {data!r}")
        if b"successfully" in data.lower():
            logger.debug(f"pty reader 捕获 success: {data!r}")
        # 自动应答 survey 的光标位置请求 \x1b[6n
        if b"\x1b[6n" in data:
            try:
                os.write(master, b"\x1b[1;1R")
                logger.debug("pty reader 应答光标位置请求")
            except OSError:
                pass


def _write_pty(master_fd: int, data: str) -> None:
    """向 pty master 写入数据（同步）。"""
    os.write(master_fd, data.encode())


def _wait_pid(pid: int, timeout: float) -> None:
    """等待指定 pid 退出（同步，最多 timeout 秒）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                return
        except ChildProcessError:
            return
        time.sleep(0.3)


def _is_process_alive(pid: int | None) -> bool:
    """检查进程是否存活。"""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _terminate_handle(handle: LoginQrHandle) -> None:
    """彻底清理：kill 进程组、关闭 pty fd。"""
    if handle._terminated:
        return
    handle._terminated = True
    if handle.pgid is not None:
        try:
            os.killpg(handle.pgid, 9)
        except (ProcessLookupError, PermissionError):
            pass
    if handle.pid is not None:
        try:
            os.waitpid(handle.pid, 0)
        except (ChildProcessError, OSError):
            pass
    if handle.master_fd is not None:
        try:
            os.close(handle.master_fd)
        except OSError:
            pass
    handle.master_fd = None


def _kill_stale_tdl_processes() -> None:
    """清理所有残留的 tdl 进程 + 损坏的 bolt 数据库锁，确保 login 能干净启动。

    两步清理：
    1. kill 所有 tdl 进程（避免进程持锁）
    2. 删除 bolt 会话数据库文件（bbolt 在进程被 kill -9 后，
       数据库文件的锁页可能处于脏状态，导致 tdl 误报
       'Current database is used by another process'）。

    注意：第 2 步会删除已登录的会话。但本函数仅在 login 前调用，
    login 本就是要创建新会话，删除旧会话是合理的（若旧会话有效，
    用户不会重新 login）。

    会话目录结构：~/.tdl/data/<namespace>（如 default）
    Windows 无 /proc，跳过。
    """
    import time
    from pathlib import Path

    if os.name != "posix":
        return
    proc_dir = "/proc"
    if not os.path.isdir(proc_dir):
        return
    current_pid = os.getpid()
    killed = 0
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
        # 匹配所有 tdl 二进制调用（login/dl/export/chat 等子命令均可能持锁或残留）
        if "tdl" in cmdline:
            try:
                os.kill(pid, 9)
                killed += 1
                logger.warning(f"清理残留 tdl 进程: PID={pid} cmd={cmdline[:80]}")
            except (ProcessLookupError, PermissionError):
                pass
    if killed:
        # 等 bolt flock 释放
        time.sleep(1)

    # 删除当前 namespace 的会话数据库文件，避免脏锁导致 login 失败。
    # login 会创建全新会话，删除旧会话不影响（旧会话有效时不会重新 login）。
    # data_dir 由 localstore 提供，形如 .../nonebot_plugin_parser/.../data
    # tdl 的会话默认在 ~/.tdl/data/<namespace>
    import os as _os

    home = _os.path.expanduser("~")
    tdl_data = Path(home) / ".tdl" / "data" / pconfig.tdl_ns
    if tdl_data.exists():
        try:
            tdl_data.unlink()
            logger.info(f"删除 tdl 会话数据库: {tdl_data}（login 将创建新会话）")
        except (PermissionError, OSError) as e:
            logger.warning(f"删除 tdl 会话数据库失败: {e}")


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
        cleaned = strip_ansi(line)
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
