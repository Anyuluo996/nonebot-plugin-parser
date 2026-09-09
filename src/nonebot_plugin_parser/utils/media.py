"""ffmpeg / ffprobe / gifsicle 子进程封装与音视频处理。

从原 ``utils.py`` 拆出：统一子进程执行（超时 + 取消时 kill），
视频/音频合并、GIF 转换与优化、H.264 转码、缩略图抽取。
"""

import asyncio
from typing import Any
from pathlib import Path

from nonebot import logger

from ._common import fmt_size, safe_unlink

# 子进程（ffmpeg/ffprobe/gifsicle）单次执行的超时上限（秒）。
# 坏输入/卡扇区时避免协程被永久挂起、子进程变孤儿。
FFMPEG_TIMEOUT = 300


async def _run_subprocess(
    cmd: list[str],
    *,
    timeout: float = FFMPEG_TIMEOUT,
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

    # process.returncode 类型为 int | None; 超时分支已 raise, 正常退出非 None。
    # 断言收敛类型, 兜底 -1 保证签名 tuple[int, bytes, bytes]。
    rc = process.returncode if process.returncode is not None else -1
    return rc, stdout, stderr


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
