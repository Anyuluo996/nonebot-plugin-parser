"""ffmpeg / ffprobe / gifsicle 子进程封装与音视频处理。

从原 ``utils.py`` 拆出：统一子进程执行（超时 + 取消时 kill），
视频/音频合并、GIF 转换与优化、H.264 转码、缩略图抽取。
"""

import re
import asyncio
from math import ceil
from uuid import uuid4
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


_ffmpeg_available_cache: bool | None = None


def ffmpeg_available() -> bool:
    """ffmpeg/ffprobe 是否可用（按 PATH 探测，进程内缓存）。

    抖音图文合成幻灯片视频前需据此决定是否走视频路径；任一缺失时解析层
    直接回退逐张发图，而不是等合成协程失败后连图也丢（合成还需要 ffprobe
    探测 BGM 时长与画布尺寸）。
    """
    global _ffmpeg_available_cache
    if _ffmpeg_available_cache is None:
        from shutil import which

        _ffmpeg_available_cache = which("ffmpeg") is not None and which("ffprobe") is not None
    return _ffmpeg_available_cache


async def probe_media_duration(path: Path) -> float | None:
    """ffprobe 探测媒体时长（秒），失败或输出非数值返回 None"""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        output = await exec_ffprobe_cmd(cmd)
        return float(output.strip())
    except (RuntimeError, ValueError):
        logger.debug(f"探测媒体时长失败: {path.name}")
        return None


async def _probe_image_size(path: Path) -> tuple[int, int] | None:
    """ffprobe 探测图片宽高，失败返回 None"""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        output = await exec_ffprobe_cmd(cmd)
    except RuntimeError:
        return None
    parts = output.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


async def media_to_slideshow(
    segments: list[tuple[Path, str]],
    audio_path: Path | None = None,
    output_path: Path | None = None,
    *,
    per_image: float = 5.0,
    max_duration: float = 60.0,
    fps: int = 5,
) -> Path:
    """静态图 + 实况视频混排序列 + 可选 BGM 合成单条轮播幻灯片视频（抖音图文用）。

    Args:
        segments: ``(路径, 类型)`` 列表, 类型 ``"image"`` 或 ``"video"``,
            按原帖顺序排列。图片按目标时长展示, 视频按自身时长原速播放
            (探不出时长的视频段丢弃降级)。
        audio_path: BGM; None 或探不出时长时降级无声。
        output_path: 产物路径; None 时取首段同目录 ``<stem>_slideshow.mp4``。
        per_image: 每张图目标展示秒数; 有 BGM 时实际取
            ``min(per_image, 总长/图片数)``。
        max_duration: 总时长上限（秒）; BGM 超长只取开头一段控制体积与合成
            耗时, <=0 视为不设上限（跟随完整 BGM）。
        fps: 输出帧率, 静态画面 5 足够。

    有 BGM 时幻灯片**总长 = min(BGM 时长, max_duration)**：BGM 短于上限时
    完整保留（音乐不截断），超长只取开头一段。图片展示
    ``min(per_image, 总长/图片数)`` 秒（图多时均摊保证每张图出现），视频按
    自身时长播放；整段序列循环快切直到幻灯片结束（对齐抖音 App 行为）。
    BGM 缺失/探测失败降级无声, 单遍播完。视频段自身音轨不保留, 全程配 BGM。
    画布取所有媒体最大宽/高（上限 1920、对齐偶数），小图黑边居中。

    每个文件作为**独立输入**由 ffmpeg 按真实格式各自解码, concat 滤镜拼接,
    每条支路统一 scale/pad/setsar/format/fps 保证 concat 输入参数一致。
    不用 concat demuxer：它按**首个文件**选解码器, 混编格式（jpeg+webp）时
    后续段被喂错解码器, 老 ffmpeg 上静默丢帧或整体失败且 rc 可为 0
    （2026-09-29 线上: 3 图只出 2 图, yun/wo4 均为 ffmpeg 4.3）。

    输出 h264(+aac) mp4，先写随机后缀临时文件、编码成功且时长校验通过后
    原子替换，避免中断残留半截文件被下次的 exists() 快速路径误判为可用。

    Raises:
        RuntimeError: ffmpeg 不可用/合成失败/产物时长异常/无法探测任何媒体
            尺寸/全部媒体段不可用。
        ValueError: segments 为空。
    """
    if not segments:
        raise ValueError("segments 为空")
    if output_path is None:
        output_path = segments[0][0].with_name(f"{segments[0][0].stem}_slideshow.mp4")
    if output_path.exists():
        return output_path

    n_img = sum(1 for _, kind in segments if kind == "image")
    audio_duration = None
    if audio_path:
        audio_duration = await probe_media_duration(audio_path)
        if audio_duration is None:
            # BGM 下载"成功"但探不出时长（如 403 HTML 存成 .mp3）：交给 ffmpeg
            # 大概率解码失败丢掉整个图文，降级为无声更符合「BGM 拿不到只损失氛围」
            logger.warning(f"BGM 无法探测时长, 疑似损坏, 降级为无声视频: {audio_path.name}")
            audio_path = None
    if not max_duration or max_duration <= 0:
        max_duration = float("inf")
    total = min(audio_duration, max_duration) if audio_duration and audio_duration > 0.1 else None
    img_per = min(per_image, total / n_img) if total and n_img else per_image

    # 探测视频段自身时长, 坏段丢弃降级 (与图片下载失败的降级同待遇)
    vid_paths = [p for p, kind in segments if kind == "video"]
    vid_durs = await asyncio.gather(*(probe_media_duration(p) for p in vid_paths))
    units: list[tuple[Path, str, float]] = []
    for p, kind in segments:
        if kind == "image":
            units.append((p, kind, img_per))
    for p, d in zip(vid_paths, vid_durs):
        if d and d > 0.1:
            units.append((p, "video", d))
        else:
            logger.warning(f"幻灯片视频段无法探测时长, 跳过: {p.name}")
    if not units:
        raise RuntimeError("幻灯片无可用媒体段")
    seq_total = sum(d for _, _, d in units)

    if total is not None:
        # 序列循环遍数: ceil(总长 / 单遍时长), 输出 -t total 戒掉末遍超出部分
        passes = max(1, ceil(total / seq_total))
    else:
        total = seq_total
        passes = 1

    canvas_w = canvas_h = 0
    for size in await asyncio.gather(*(_probe_image_size(p) for p, _, _ in units)):
        if size:
            canvas_w = max(canvas_w, size[0])
            canvas_h = max(canvas_h, size[1])
    if canvas_w <= 0 or canvas_h <= 0:
        raise RuntimeError("无法探测任何媒体尺寸")
    canvas_w = max(2, min(canvas_w, 1920)) // 2 * 2
    canvas_h = max(2, min(canvas_h, 1920)) // 2 * 2

    # 每段独立输入; concat 滤镜要求各段分辨率/帧率/像素格式一致, 故每条支路
    # 统一 scale+pad(画布)+setsar+format, 且 fps 归一在 concat **之前**
    # (图片支路 1fps、视频支路原生 fps, 不归一直接 concat 会参数失配)。
    # 临时文件名带随机后缀：同一 note 并发合成时固定名会互相踩踏/被另一方
    # 删掉正被读的输入。
    tag = uuid4().hex[:8]
    tmp_path = output_path.with_name(f"{output_path.stem}_{tag}_tmp.mp4")
    cmd: list[str] = ["ffmpeg", "-y"]
    chains: list[str] = []
    sequence: list[tuple[Path, str, float]] = units * passes
    for i, (p, kind, d) in enumerate(sequence):
        if kind == "image":
            # -framerate 1 必须带: image2 的 -loop 1 默认按 25fps 把每张图
            # **重复解码** per×25 次, yun 实测 39 输入 × 5s 达 ~50s 直接撞穿
            # 30s 的 video_send_timeout; 降到 1fps 后每图只解 ceil(per) 次。
            cmd += ["-framerate", "1", "-loop", "1", "-t", f"{d:.3f}", "-i", str(p)]
        else:
            cmd += ["-i", str(p)]
        chains.append(
            f"[{i}:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,format=yuv420p,fps={fps}[s{i}]"
        )
    m = len(sequence)
    if audio_path:
        # BGM 短于视频时无限循环，靠输出 -t 在视频末端精确截断
        cmd += ["-stream_loop", "-1", "-i", str(audio_path)]
    filter_complex = ";".join(chains) + (
        f";{''.join(f'[s{i}]' for i in range(m))}concat=n={m}:v=1:a=0,format=yuv420p[v]"
    )
    cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
    if audio_path:
        cmd += ["-map", f"{m}:a", "-c:a", "aac", "-b:a", "128k"]
    # veryfast: 幻灯片总长跟随 BGM 后可达数分钟, 编码时长须压在
    # video_send_timeout(默认 30s) 内; 静态画面下与 medium 视觉无差
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-tune",
        "stillimage",
        "-movflags",
        "+faststart",
        "-t",
        f"{total:.3f}",
        str(tmp_path),
    ]

    try:
        await exec_ffmpeg_cmd(cmd)
        # 输入打开失败等异常路径仍可能 rc=0（静默产出残缺视频），
        # 用产物时长兜底校验：半截视频不允许 replace 进缓存被 exists() 永久命中
        got = await probe_media_duration(tmp_path)
        if got is None or got < total * 0.5:
            raise RuntimeError(f"合成产物时长异常: 期望 ~{total:.1f}s, 实际 {got}")
        await asyncio.to_thread(tmp_path.replace, output_path)
    except BaseException:
        await safe_unlink(tmp_path)
        raise

    n_vid = len(units) - n_img
    parts = [f"{n_img} 图 × {img_per:.1f}s" if n_img else "", f"{n_vid} 视频" if n_vid else ""]
    logger.success(
        f"幻灯片视频合成成功: {output_path.name}, {' + '.join(x for x in parts if x)}, "
        f"单遍 {seq_total:.1f}s × {passes} 遍, {fmt_size(output_path)}"
    )
    return output_path


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


async def has_audible_audio_stream(video_path: Path, *, silent_below_db: float = -45.0) -> bool:
    """检测视频是否含「可闻」音频流（BGM 合并决策专用）。

    与 has_audio_stream 的差异: 抖音实况照片的 mp4 普遍带一条**全静音**
    AAC 轨（volumedetect 实测 mean=max=-91.0 dB 的数字零样本），只查流
    存在性会把它当成原声跳过 BGM 合并、发出无声视频。这里用 volumedetect
    的 max_volume 判定：无音轨或低于阈值都返回 False（视为需要补 BGM）；
    探测失败（解码错误等）保守返回 True，宁可不合并也不误删可闻原声。

    Args:
        video_path: 视频文件路径。
        silent_below_db: max_volume 低于该分贝值视为静音，默认 -45。

    Returns:
        是否含可闻音频流。
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(video_path),
        "-map",
        "0:a:0",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    try:
        _rc, _stdout, stderr = await _run_subprocess(cmd)
    except FileNotFoundError:
        # ffmpeg 缺失时无从判定, 视为有原声 (与旧 has_audio_stream 决策一致)
        return True
    text = stderr.decode(errors="replace")
    m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", text)
    if not m:
        # 无音轨: ffmpeg 对 -map 0:a:0 报 "matches no streams" 且 rc != 0
        if "matches no streams" in text:
            return False
        return True
    return float(m.group(1)) >= silent_below_db


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
    shortest: bool = False,
    cleanup_inputs: bool = True,
) -> None:
    """合并视频和音频

    Args:
        v_path: 视频文件 (输入)
        a_path: 音频文件 (输入)
        output_path: 输出文件
        shortest: 音频比视频长时截到视频长。抖音实况照片的 BGM 是整曲
            而视频只有单张照片级时长, 不截会产出视频定格的拖尾长音频。
        cleanup_inputs: 合并成功后删除输入文件; 输入是下载缓存文件时必须
            False —— 删除会破坏缓存, 且并发合并共享同一 BGM 文件时互相踩。
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
    ]
    if shortest:
        cmd.append("-shortest")
    # 固定输出名并发合并同一目标会互相写坏产物, 写随机后缀临时文件、
    # 成功后原子替换 (与 media_to_slideshow 同套路)
    tag = uuid4().hex[:8]
    tmp_path = output_path.with_name(f"{output_path.stem}_{tag}_tmp.mp4")
    cmd.append(str(tmp_path))

    await exec_ffmpeg_cmd(cmd)
    await asyncio.to_thread(tmp_path.replace, output_path)
    if cleanup_inputs:
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
