"""tdl (Telegram Downloader) 二进制封装。

tdl 是 iyear/tdl 提供的 Telegram 下载 CLI，通过子进程调用。
注意 tdl 不读取 http_proxy/https_proxy 环境变量，代理必须通过 --proxy 显式传入。
"""

import json
import shutil
from pathlib import Path

from nonebot import logger

from ..utils import generate_file_name
from ..config import pconfig
from ..exception import ParseException

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

    tdl 会打印 ASCII 二维码到 stdout 并阻塞等待 Telegram App 扫码确认。
    本函数流式读取 stdout，进程在用户扫码确认后退出（exit 0）；
    超时则 kill 进程，把已捕获的 stdout（含二维码）返回，由调用方渲染。

    Args:
        timeout: 等待扫码的最长时间（秒），超时则返回失败提示

    Returns:
        tuple[bool, str]:
            - (True, "Telegram 登录成功")：登录成功（tdl exit 0）
            - (False, ascii_qr)：超时/失败但已捕获二维码，调用方渲染 PNG 发送
            - (False, reason)：失败且无二维码，第二项为原因
    """
    import asyncio

    if not is_tdl_available():
        raise ParseException("tdl 不可用，无法执行登录")

    cmd = [*_build_base_args(), "login", "--type", "qr"]
    logger.info(f"tdl login qr 启动（等待扫码，超时 {timeout}s）")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ParseException(f"未找到 tdl 二进制（{pconfig.tdl_path}），请安装 tdl 或配置 parser_tdl_path") from e

    assert proc.stdout is not None
    assert proc.stderr is not None

    # 流式读取 stdout（二维码 + 状态行），stderr 异步读后丢弃
    stdout_chunks: list[bytes] = []

    async def _drain(stream, sink: list[bytes]) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            sink.append(chunk)

    stderr_task = asyncio.create_task(_drain(proc.stderr, []))
    stdout_task = asyncio.create_task(_drain(proc.stdout, stdout_chunks))

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        exited_cleanly = True
    except asyncio.TimeoutError:
        exited_cleanly = False
        proc.kill()
        await proc.wait()

    # 等待两个 drain 任务收尾（EOF）
    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    text = b"".join(stdout_chunks).decode(errors="replace")
    qr = extract_qr_ascii(text)

    if exited_cleanly and proc.returncode == 0:
        return True, "Telegram 登录成功"
    # 失败/超时：若已有二维码，返回给调用方渲染（提示超时由调用方补充）
    if qr:
        return False, qr
    if not exited_cleanly:
        return False, "tdl 登录超时，未捕获到二维码，请检查代理/网络后重试"
    return False, f"tdl 登录失败 (exit {proc.returncode})"


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
