import re
import asyncio
import hashlib
import importlib.util
from typing import Any, TypeVar
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urlparse

from nonebot import logger

K = TypeVar("K")
V = TypeVar("V")


class LimitedSizeDict(OrderedDict[K, V]):
    """
    定长字典
    """

    def __init__(self, *args, max_size=20, **kwargs):
        self.max_size = max_size
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: K, value: V):
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)  # 移除最早添加的项


def keep_zh_en_num(text: str) -> str:
    """
    保留字符串中的中英文和数字
    """
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\-_]", "", text.replace(" ", "_"))


async def safe_unlink(path: Path):
    """
    安全删除文件
    """
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception:
        logger.warning(f"删除 {path} 失败")


async def exec_ffmpeg_cmd(cmd: list[str]) -> None:
    """执行命令

    Args:
        cmd (list[str]): 命令序列
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        return_code = process.returncode
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 未安装或无法找到可执行文件")

    if return_code != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"ffmpeg 执行失败: {error_msg}")


async def exec_ffprobe_cmd(cmd: list[str]) -> str:
    """执行 ffprobe 命令

    Args:
        cmd (list[str]): 命令序列

    Returns:
        str: ffprobe 输出
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return_code = process.returncode
    except FileNotFoundError:
        raise RuntimeError("ffprobe 未安装或无法找到可执行文件")

    if return_code != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"ffprobe 执行失败: {error_msg}")

    return stdout.decode()


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


async def convert_video_to_gif(
    video_path: Path,
    output_path: Path | None = None,
    fps: int = 15,
    width: int = 480,
    optimize: bool = True,
) -> Path:
    """将视频转换为高质量 GIF（使用 palettegen 滤镜）

    Args:
        video_path (Path): 输入视频路径
        output_path (Path | None): 输出 GIF 路径，默认为视频同目录的 .gif 文件
        fps (int): 输出 GIF 的帧率，默认 15
        width (int): 输出 GIF 的宽度，默认 480（高度自动计算）
        optimize (bool): 是否优化 GIF，默认 True

    Returns:
        Path: 输出 GIF 文件路径
    """
    if output_path is None:
        output_path = video_path.with_suffix(".gif")

    logger.info(f"转换视频到 GIF: {video_path.name} -> {output_path.name}")

    # 生成调色板的临时文件
    palette_path = video_path.with_name(f"{video_path.stem}_palette.png")

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

    # 清理临时调色板文件
    await safe_unlink(palette_path)

    logger.success(f"GIF 转换成功: {output_path.name}, {fmt_size(output_path)}")

    # 如果启用了优化，进一步使用 gifsicle 优化（如果可用）
    if optimize:
        try:
            await optimize_gif(output_path)
        except RuntimeError:
            logger.debug("gifsicle 不可用，跳过优化")

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

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()

    if process.returncode == 0:
        # 替换原文件
        await asyncio.to_thread(temp_path.replace, gif_path)
        logger.success(f"GIF 优化成功: {gif_path.name}, {fmt_size(gif_path)}")
    else:
        # 清理临时文件
        await safe_unlink(temp_path)
        raise RuntimeError(f"gifsicle 执行失败")


async def merge_av(
    *,
    v_path: Path,
    a_path: Path,
    output_path: Path,
) -> None:
    """合并视频和音频

    Args:
        v_path (Path): 视频文件路径
        a_path (Path): 音频文件路径
        output_path (Path): 输出文件路径
    """
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
    """合并视频和音频，并使用 H.264 编码

    Args:
        v_path (Path): 视频文件路径
        a_path (Path): 音频文件路径
        output_path (Path): 输出文件路径
    """
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
    """将视频重新编码到 h264

    Args:
        video_path (Path): 视频路径

    Returns:
        Path: 编码后的视频路径
    """
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
    """格式化文件大小

    Args:
        video_path (Path): 视频路径
    """
    return f"大小: {file_path.stat().st_size / 1024 / 1024:.2f} MB"


def generate_file_name(url: str, default_suffix: str = "") -> str:
    """根据 url 生成文件名

    Args:
        url (str): url
        default_suffix (str): 默认后缀. Defaults to "".

    Returns:
        str: 文件名
    """
    # 根据 url 获取文件后缀
    path = Path(urlparse(url).path)
    suffix = path.suffix if path.suffix else default_suffix
    # 获取 url 的 md5 值
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    file_name = f"{url_hash}{suffix}"
    return file_name


def write_json_to_data(data: dict[str, Any] | str, file_name: str):
    """将数据写入数据目录

    Args:
        data (dict[str, Any] | str): 数据
        file_name (str): 文件名
    """
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
