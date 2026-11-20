"""convert_video_to_gif 回归：超短高帧率源不得被 fps 抽帧塌缩成单帧。

背景（2026-11-21 线上案例）：X 的抖动 meme GIF 实为 2-8 帧 / 0.1-0.4s / 25-33fps
的超短循环 mp4，旧实现固定 fps=15 做 CFR 重采样，0.09s 的源只够采到 1 个输出
样本点，3 帧源被塌缩成 1 帧静态图，用户侧表现为"3 个 GIF 有 2 个是图片"。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


def _frame_count(path: Path) -> int:
    """统计文件视频流的帧/包数量（GIF 用包数即帧数）。"""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_packets",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_packets",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return int(out.stdout.strip() or "0")


def _make_source(path: Path, *, duration: float, rate: int) -> Path:
    """用 lavfi testsrc 生成 H.264 测试源。"""
    src = path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=64x64:rate={rate}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return src


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe 不可用")
@pytest.mark.asyncio
async def test_short_high_fps_source_keeps_all_frames(tmp_path):
    """超短(0.12s)@33fps 源：转换后帧数不得少于源帧数（旧实现只剩 1 帧）。"""
    from nonebot_plugin_parser.utils.media import convert_video_to_gif

    src = _make_source(tmp_path, duration=0.12, rate=33)
    src_frames = _frame_count(src)
    assert src_frames >= 2, f"测试源生成异常: 仅 {src_frames} 帧"

    gif = await convert_video_to_gif(src)

    gif_frames = _frame_count(gif)
    assert gif_frames >= src_frames, f"超短源被 fps 抽帧塌缩: 源 {src_frames} 帧 -> GIF 仅 {gif_frames} 帧"


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe 不可用")
@pytest.mark.asyncio
async def test_normal_video_still_downsamples_fps(tmp_path):
    """正常长度(2s)@30fps 源：仍按 fps=15 抽帧压缩体积（约为源帧数一半）。"""
    from nonebot_plugin_parser.utils.media import convert_video_to_gif

    src = _make_source(tmp_path, duration=2, rate=30)
    src_frames = _frame_count(src)
    assert src_frames >= 30, f"测试源生成异常: 仅 {src_frames} 帧"

    gif = await convert_video_to_gif(src)

    gif_frames = _frame_count(gif)
    assert gif_frames < src_frames, "正常视频未按 15fps 抽帧，GIF 体积会翻倍"
    assert gif_frames >= src_frames // 3, f"抽帧过度: 源 {src_frames} 帧 -> GIF 仅 {gif_frames} 帧"
