"""PIL 图像处理：ASCII 二维码渲染与 Pixiv ugoira 动图转换。

PIL 为可选依赖：未安装时函数在调用时抛 RuntimeError（``PIL_AVAILABLE`` 守卫）。
"""

import io
import zipfile
from typing import TYPE_CHECKING, Any
from pathlib import Path

from nonebot import logger

from ._common import fmt_size

if TYPE_CHECKING:
    # 仅类型检查期导入, 让 pyright 知道 Image 的真实类型 (PIL.Image 模块),
    # 从而能正确解析 Image.new / Image.open / Image.Image / Image.Resampling。
    from PIL import Image
    from PIL.Image import Image as PILImage

try:
    from PIL import Image  # type: ignore[no-redef]

    PIL_AVAILABLE = True
except ImportError:
    # PIL 缺失时给 Image 一个与模块结构兼容的占位, 避免后续 unbound 引用;
    # 实际调用前每个函数都有 PIL_AVAILABLE 守卫 raise, 不会真的走到占位属性。
    # 用 TYPE_CHECKING 导入保证类型检查期 Image 仍是 PIL 模块类型。
    from types import ModuleType

    Image = ModuleType("Image")  # type: ignore[assignment,misc]
    PIL_AVAILABLE = False


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
    assert pixels is not None  # Image.new 返回 ImageFile, .load() 在已加载图像上必非 None
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

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    return buf.getvalue()


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

    images: list[PILImage] = []
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
