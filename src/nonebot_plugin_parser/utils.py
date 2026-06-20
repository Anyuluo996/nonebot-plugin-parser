import re
import asyncio
import hashlib
import zipfile
import importlib.util
from typing import Any, TypeVar
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urlparse

from anyio import Path as AnyioPath
from nonebot import logger

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

K = TypeVar("K")
V = TypeVar("V")


class LimitedSizeDict(OrderedDict[K, V]):
    def __init__(self, *args, max_size=20, **kwargs):
        self.max_size = max_size
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: K, value: V):
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)  # 移除最早添加的项


def keep_zh_en_num(text: str) -> str:
    """保留字符串中的中英文和数字"""
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\-_]", "", text.replace(" ", "_"))


async def safe_unlink(path: Path):
    """安全删除文件"""
    await AnyioPath(path).unlink(missing_ok=True)


# 子进程（ffmpeg/ffprobe/gifsicle）单次执行的超时上限（秒）。
# 坏输入/卡扇区时避免协程被永久挂起、子进程变孤儿。
FFMPEG_TIMEOUT = 300


async def _run_subprocess(
    cmd: list[str],
    *,
    timeout: float = FFMPEG_TIMEOUT,  # noqa: ASYNC109 语义即"超时秒数"，非并发原语
    stdin_devnull: bool = True,
) -> tuple[int, bytes, bytes]:
    """统一执行外部子进程：带超时、取消时强制 kill、回收 stdout/stderr 管道。

    Args:
        cmd: 命令序列（第一项为可执行文件）。
        timeout: 超时秒数，超时后 kill 子进程并抛 ``asyncio.TimeoutError``。
        stdin_devnull: 是否把 stdin 接到 DEVNULL（避免子进程等待 stdin 挂起）。

    Returns:
        (returncode, stdout_bytes, stderr_bytes)。

    Raises:
        FileNotFoundError: 可执行文件不存在。
        asyncio.TimeoutError: 超时。
        RuntimeError: 返回码非 0。
    """
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if stdin_devnull:
        kwargs["stdin"] = asyncio.subprocess.DEVNULL

    process = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # 超时或被取消（外层超时/用户撤回）：强制终止子进程，避免变孤儿；
        # wait 回收资源/关闭 stdout/stderr 管道，再重新抛出原异常
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await process.wait()
        except BaseException:
            pass
        raise

    return process.returncode, stdout, stderr


async def exec_ffmpeg_cmd(cmd: list[str]) -> None:
    """执行 ffmpeg 命令"""
    try:
        return_code, _stdout, stderr = await _run_subprocess(cmd)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 未安装或无法找到可执行文件")

    if return_code != 0:
        error_msg = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg 执行失败: {error_msg}")


async def exec_ffprobe_cmd(cmd: list[str]) -> str:
    """执行 ffprobe 命令

    Args:
        cmd (list[str]): 命令序列

    Returns:
        str: ffprobe 输出
    """
    try:
        return_code, stdout, stderr = await _run_subprocess(cmd)
    except FileNotFoundError:
        raise RuntimeError("ffprobe 未安装或无法找到可执行文件")

    if return_code != 0:
        error_msg = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffprobe 执行失败: {error_msg}")

    return stdout.decode(errors="replace")


async def has_audio_stream(video_path: Path) -> bool:
    """检测视频文件是否包含音频流

    Args:
        video_path (Path): 视频文件路径

    Returns:
        bool: 是否包含音频流
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",  # 只选择音频流
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(video_path),
    ]

    try:
        output = await exec_ffprobe_cmd(cmd)
        return bool(output.strip())
    except RuntimeError:
        logger.warning(f"检测音频流失败: {video_path}")
        return False


async def extract_video_thumbnail(video_path: Path, output_path: Path | None = None) -> Path | None:
    """从视频抽取首帧作为缩略图（用于无封面 URL 的视频，如 Telegram）。

    Args:
        video_path: 视频文件路径。
        output_path: 输出缩略图路径，默认为视频同目录的 ``<stem>_thumb.jpg``。

    Returns:
        缩略图路径；ffmpeg 不可用或抽取失败时返回 None（不抛异常，降级为无封面）。
    """
    if output_path is None:
        output_path = video_path.with_name(f"{video_path.stem}_thumb.jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "00:00:01",  # 跳到第 1 秒（避开黑场），避免首帧全黑
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-vf",
        "scale=800:-1",  # 宽度 800，高度按比例；与卡片内容宽度一致
        "-q:v",
        "3",
        str(output_path),
    ]

    try:
        await exec_ffmpeg_cmd(cmd)
    except (RuntimeError, FileNotFoundError):
        logger.debug(f"抽取视频缩略图失败（ffmpeg 不可用或视频异常）: {video_path.name}")
        return None

    if output_path.exists():
        logger.debug(f"视频缩略图抽取成功: {output_path.name}")
        return output_path
    return None


async def convert_video_to_gif(
    video_path: Path,
    output_path: Path | None = None,
    fps: int = 15,
    width: int = 480,
    optimize: bool = False,
) -> Path:
    """将视频转换为高质量 GIF（使用 palettegen 滤镜）

    Args:
        video_path (Path): 输入视频路径
        output_path (Path | None): 输出 GIF 路径，默认为视频同目录的 .gif 文件
        fps (int): 输出 GIF 的帧率，默认 15
        width (int): 输出 GIF 的宽度，默认 480（高度自动计算）
        optimize (bool): 是否优化 GIF，默认 False

    Returns:
        Path: 输出 GIF 文件路径
    """
    if output_path is None:
        output_path = video_path.with_suffix(".gif")

    logger.info(f"转换视频到 GIF: {video_path.name} -> {output_path.name}")

    # 生成调色板的临时文件
    palette_path = video_path.with_name(f"{video_path.stem}_palette.png")

    try:
        # 第一步：生成调色板
        # 使用 palettegen 滤镜生成自定义调色板，提高 GIF 质量
        palette_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
            str(palette_path),
        ]

        await exec_ffmpeg_cmd(palette_cmd)

        # 第二步：使用调色板生成 GIF
        # 使用 paletteuse 滤镜应用自定义调色板
        gif_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(palette_path),
            "-lavfi",
            f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
            str(output_path),
        ]

        await exec_ffmpeg_cmd(gif_cmd)
    finally:
        # 无论成功失败都清理临时调色板文件（原先异常路径会残留 _palette.png）
        await safe_unlink(palette_path)

    logger.success(f"GIF 转换成功: {output_path.name}, {fmt_size(output_path)}")

    # 如果启用了优化，进一步使用 gifsicle 优化（如果可用）
    if optimize:
        try:
            await optimize_gif(output_path)
        except (RuntimeError, FileNotFoundError):
            logger.debug("gifsicle 不可用或优化失败，跳过优化")

    return output_path


async def optimize_gif(gif_path: Path) -> None:
    """使用 gifsicle 优化 GIF 文件

    Args:
        gif_path (Path): GIF 文件路径
    """
    # 创建临时文件
    temp_path = gif_path.with_name(f"{gif_path.stem}_temp.gif")

    cmd = [
        "gifsicle",
        "-O3",  # 最大优化级别
        "--lossy=30",  # 有损压缩，30 表示损失 30% 的质量
        "--colors",
        "256",  # 限制颜色数量
        "-o",
        str(temp_path),
        str(gif_path),
    ]

    try:
        return_code, _stdout, stderr = await _run_subprocess(cmd)
    except FileNotFoundError:
        raise RuntimeError("gifsicle 未安装或无法找到可执行文件")

    if return_code == 0:
        # 替换原文件
        await asyncio.to_thread(temp_path.replace, gif_path)
        logger.success(f"GIF 优化成功: {gif_path.name}, {fmt_size(gif_path)}")
    else:
        # 失败时清理可能残留的临时文件
        await safe_unlink(temp_path)
        error_msg = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"gifsicle 执行失败: {error_msg}")


async def merge_av(
    *,
    v_path: Path,
    a_path: Path,
    output_path: Path,
) -> None:
    """合并视频和音频"""
    logger.info(f"Merging {v_path.name} and {a_path.name} to {output_path.name}")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(v_path),
        "-i",
        str(a_path),
        "-c",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        str(output_path),
    ]

    await exec_ffmpeg_cmd(cmd)
    await asyncio.gather(safe_unlink(v_path), safe_unlink(a_path))
    logger.success(f"Merged {output_path.name}, {fmt_size(output_path)}")


async def merge_av_h264(
    *,
    v_path: Path,
    a_path: Path,
    output_path: Path,
) -> None:
    """合并视频和音频，并使用 H.264 编码"""
    logger.info(f"Merging {v_path.name} and {a_path.name} to {output_path.name} with H.264")

    # 修改命令以确保视频使用 H.264 编码
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(v_path),
        "-i",
        str(a_path),
        "-c:v",
        "libx264",  # 明确指定使用 H.264 编码
        "-preset",
        "medium",  # 编码速度和质量的平衡
        "-crf",
        "23",  # 质量因子，值越低质量越高
        "-c:a",
        "aac",  # 音频使用 AAC 编码
        "-b:a",
        "128k",  # 音频比特率
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        str(output_path),
    ]

    await exec_ffmpeg_cmd(cmd)
    await asyncio.gather(safe_unlink(v_path), safe_unlink(a_path))
    logger.success(f"Merged {output_path.name} with H.264, {fmt_size(output_path)}")


async def encode_video_to_h264(video_path: Path) -> Path:
    """将视频重新编码到 h264"""
    output_path = video_path.with_name(f"{video_path.stem}_h264{video_path.suffix}")
    if output_path.exists():
        return output_path
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        str(output_path),
    ]
    await exec_ffmpeg_cmd(cmd)
    logger.success(f"视频重新编码为 H.264 成功: {output_path}, {fmt_size(output_path)}")
    await safe_unlink(video_path)
    return output_path


def fmt_size(file_path: Path) -> str:
    """格式化文件大小"""
    return f"大小: {file_path.stat().st_size / 1024 / 1024:.2f} MB"


def fmt_duration(duration: float) -> str:
    """格式化媒体时长，超过 1 小时后显示为 h:mm:ss。"""
    total_seconds = max(int(duration), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def generate_file_name(url: str, default_suffix: str = "") -> str:
    """根据 url 生成文件名"""

    # 根据 url 获取文件后缀
    path = Path(urlparse(url).path)
    suffix = path.suffix if path.suffix else default_suffix
    # 获取 url 的 md5 值
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    file_name = f"{url_hash}{suffix}"
    return file_name


def render_qr_ascii_to_png(ascii_qr: str, scale: int = 10, border: int = 4) -> bytes:
    """把 tdl 输出的 ASCII 二维码渲染成 PNG 图片字节。

    tdl 的二维码由 4 种 Unicode block 字符构成，每个字符代表 2 个像素行：
        ' ' (空格)      上下半都白
        '▀' (U+2580)    上半黑、下半白
        '▄' (U+2584)    上半白、下半黑
        '█' (U+2588)    上下半都黑

    Args:
        ascii_qr: 由 extract_qr_ascii 提取的二维码文本（多行）
        scale: 每个二维码模块放大的像素倍数（提升扫码成功率）
        border: 四周留白（模块数），便于扫码器识别

    Returns:
        bytes: PNG 图片字节流
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL (Pillow) 未安装，无法渲染二维码")

    lines = [line for line in ascii_qr.splitlines() if line]
    if not lines:
        raise ValueError("二维码文本为空")

    width = max(len(line) for line in lines)
    # 每个字符 = 2 个像素行（上半 + 下半）
    height = len(lines) * 2

    # 1. 先画 1:1 的位图
    img = Image.new("1", (width, height), 1)  # 模式 1：0=黑 1=白
    pixels = img.load()
    for row_idx, line in enumerate(lines):
        for col, ch in enumerate(line):
            upper = ch in ("█", "▀")  # 上半黑
            lower = ch in ("█", "▄")  # 下半黑
            if col < width:
                pixels[col, row_idx * 2] = 0 if upper else 1
                pixels[col, row_idx * 2 + 1] = 0 if lower else 1

    # 2. 加白边 + 放大
    bordered_w = width + border * 2
    bordered_h = height + border * 2
    final_w = bordered_w * scale
    final_h = bordered_h * scale
    final = Image.new("RGB", (final_w, final_h), (255, 255, 255))
    # 先把 1:1 图加边
    padded = Image.new("1", (bordered_w, bordered_h), 1)
    padded.paste(img, (border, border))
    # 放大到最终尺寸
    resized = padded.resize((final_w, final_h), Image.Resampling.NEAREST).convert("RGB")
    final.paste(resized, (0, 0))

    import io

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    return buf.getvalue()


def write_json_to_data(data: dict[str, Any] | str, file_name: str):
    """将数据写入数据目录"""
    import json

    from .config import pconfig

    path = pconfig.data_dir / file_name
    if isinstance(data, str):
        data = json.loads(data)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    logger.success(f"数据写入 {path} 成功")


def is_module_available(module_name: str) -> bool:
    """检查模块是否可用"""
    return importlib.util.find_spec(module_name) is not None


async def convert_ugoira_to_gif(
    zip_path: Path,
    frames: list[dict[str, Any]],
    output_path: Path | None = None,
) -> Path:
    """将 Pixiv 动图 ZIP 包转换为 GIF

    Args:
        zip_path: 动图 ZIP 文件路径
        frames: 帧信息列表，如 [{"file": "000000.jpg", "delay": 1000}, ...]
                delay 单位为毫秒
        output_path: 输出 GIF 路径，默认为 ZIP 同目录的 .gif 文件

    Returns:
        Path: 输出 GIF 文件路径
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL (Pillow) 未安装，无法转换动图为 GIF")

    if output_path is None:
        output_path = zip_path.with_suffix(".gif")

    logger.info(f"转换动图到 GIF: {zip_path.name} -> {output_path.name}")

    if not zip_path.exists():
        raise FileNotFoundError(f"动图 ZIP 文件不存在: {zip_path}")

    images: list[Image.Image] = []
    durations: list[int] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for frame in frames:
            file_name = frame.get("file", "")
            delay_ms = int(frame.get("delay", 100))
            if not file_name:
                continue
            try:
                with zf.open(file_name) as img_file:
                    img = Image.open(img_file)
                    images.append(img.convert("P"))
                    # PIL ImageSequence 会用到 duration 参数
                    durations.append(max(delay_ms // 10, 1))
            except KeyError:
                logger.warning(f"动图帧文件不存在于 ZIP 中: {file_name}")

    if not images:
        raise RuntimeError(f"动图 ZIP 中未找到任何帧: {zip_path}")

    if len(images) == 1:
        images[0].save(output_path, save_all=True, durations=durations)
    else:
        images[0].save(
            output_path,
            save_all=True,
            append_images=images[1:],
            duration=durations,
            loop=0,
            optimize=False,
        )

    logger.success(f"动图 GIF 转换成功: {output_path.name}, {fmt_size(output_path)}")
    return output_path


def extract_ugoira_thumbnail(
    zip_path: Path,
    frames: list[dict[str, Any]],
) -> Path:
    """从 Ugoira ZIP 中提取第一帧作为缩略图

    Args:
        zip_path: 动图 ZIP 文件路径
        frames: 帧信息列表，如 [{"file": "000000.jpg", "delay": 1000}, ...]

    Returns:
        Path: 缩略图文件路径 (.thumb.jpg)
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL (Pillow) 未安装，无法提取缩略图")

    thumb_path = zip_path.with_name(f"{zip_path.stem}.thumb.jpg")
    if thumb_path.exists():
        return thumb_path

    if not zip_path.exists():
        raise FileNotFoundError(f"动图 ZIP 文件不存在: {zip_path}")

    first_frame = frames[0] if frames else None
    if not first_frame:
        raise RuntimeError(f"动图帧信息为空: {zip_path}")

    file_name = first_frame.get("file", "")
    if not file_name:
        raise RuntimeError(f"动图第一帧文件名无效: {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            with zf.open(file_name) as img_file:
                img = Image.open(img_file)
                img = img.convert("RGB")
                img.save(thumb_path, "JPEG", quality=85)
        except KeyError:
            raise RuntimeError(f"动图第一帧文件不存在于 ZIP 中: {file_name}")

    logger.debug(f"提取动图缩略图: {thumb_path.name}")
    return thumb_path
