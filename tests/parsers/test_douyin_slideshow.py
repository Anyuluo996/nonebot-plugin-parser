"""抖音图文（静态图 + BGM）合成幻灯片视频发送的测试。

覆盖三层：

- parse_slides 决策：有 BGM + 开关开 + ffmpeg 可用 → 合成视频替代逐张发图；
  开关关 / ffmpeg 缺失 / 无 BGM → 回退逐张发图（旧行为）
- ``_compose_slideshow`` 降级链：单图失败用剩余图继续、BGM 失败降级无声、
  全图失败抛 DownloadException
- ``images_to_slideshow`` 真实 ffmpeg 合成（本机无 ffmpeg 时 skip）
"""

import json
import asyncio
from shutil import which
from typing import ClassVar
from pathlib import Path

import pytest

# nonebot 插件加载依赖 conftest 先初始化, 插件内模块一律在测试函数内导入

# 纯静态图文（旧 slides 格式）: 3 张无 video 的图 + music.play_url。
# 字段名是 open-api 形态实测真实形状 (2026-09-27 slides/7689698548245879931);
# 曾误写成 play_addr (Video 侧字段名), msgspec 静默丢弃导致线上 BGM 恒丢。
_STATIC_NOTE_PAYLOAD = {
    "aweme_detail": {
        "author": {
            "nickname": "测试作者",
            "avatar_thumb": {"url_list": ["https://example.com/avatar.jpg"]},
        },
        "desc": "图文描述 #标签",
        "create_time": 1734761606,
        "images": [{"url_list": [f"https://p3-pc-sign.douyinpic.com/slides{i}.jpg"]} for i in range(3)],
        "music": {
            "play_url": {
                "uri": "https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/bgm_001.mp3",
                "url_list": ["https://www.douyin.com/aweme/v1/play/?music_id=bgm_001"],
            }
        },
    }
}

_STATIC_NOTE_VID = "7450744229229235000"


class _MockResp:
    status_code = 200

    def __init__(self, raw: bytes):
        self.content = raw
        self.text = raw.decode("utf-8")

    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

    @property
    def url(self):
        return "https://www.douyin.com/aweme/v1/web/aweme/detail/"


def _make_parser(monkeypatch, *, ffmpeg_ok=True, payload=None):
    """构造 mock 好请求/下载层的 DouyinParser, 返回 (parser, downloads 记录)。"""
    from nonebot_plugin_parser import utils as utils_mod
    from nonebot_plugin_parser.parsers import DouyinParser

    parser = DouyinParser()
    raw = json.dumps(payload or _STATIC_NOTE_PAYLOAD).encode("utf-8")

    async def _fake_request(url, *args, **kwargs):
        if "aweme/v1/web/aweme/detail" in str(url):
            return _MockResp(raw)
        raise RuntimeError(f"unexpected URL: {url}")

    downloads = {"img": [], "audio": []}

    async def _img(url, *args, **kwargs):
        downloads["img"].append(url)
        # 路径由 URL 确定, 断言不依赖任务调度顺序
        return Path(f"/fake/{url.rsplit('/', 1)[-1]}")

    async def _audio(url, *args, **kwargs):
        downloads["audio"].append(url)
        return Path("/fake/bgm.mp3")

    monkeypatch.setattr(parser, "request", _fake_request)
    monkeypatch.setattr(parser.downloader, "download_img", lambda url, **kw: asyncio.create_task(_img(url, **kw)))
    monkeypatch.setattr(parser.downloader, "download_audio", lambda url, **kw: asyncio.create_task(_audio(url, **kw)))
    monkeypatch.setattr(utils_mod, "ffmpeg_available", lambda: ffmpeg_ok)
    return parser, downloads


async def test_slideshow_replaces_images_when_bgm_present(monkeypatch):
    """有 BGM + 开关开 + ffmpeg 可用 → 单条合成视频替代逐张发图。"""
    from nonebot_plugin_parser import utils as utils_mod
    from nonebot_plugin_parser.config import pconfig

    parser, downloads = _make_parser(monkeypatch, ffmpeg_ok=True)

    composed = {}

    async def _fake_compose(image_paths, audio_path, output_path, **kwargs):
        composed["image_paths"] = list(image_paths)
        composed["audio_path"] = audio_path
        return Path("/fake/slideshow.mp4")

    monkeypatch.setattr(utils_mod, "images_to_slideshow", _fake_compose)
    monkeypatch.setattr(pconfig, "parser_douyin_note_slideshow", True)

    result = await parser.parse_slides(_STATIC_NOTE_VID)

    assert result.img_contents == [], "有 BGM 时静态图应被合成视频替代, 不再逐张发图"
    assert len(result.video_contents) == 1, "应产出 1 条合成视频内容"

    video = result.video_contents[0]
    assert await video.get_path() == Path("/fake/slideshow.mp4")
    assert downloads["audio"], "BGM 应被下载"
    assert "music_id=bgm_001" in downloads["audio"][0]
    # 封面复用首图下载任务, 3 张 slides 图之外没有额外图片下载(头像除外)
    slides_downloads = [u for u in downloads["img"] if "slides" in u]
    assert len(slides_downloads) == 3
    assert await video.get_cover_path() == Path("/fake/slides0.jpg")
    # 合成拿到全部 3 张图与 BGM
    assert len(composed["image_paths"]) == 3
    assert composed["audio_path"] == Path("/fake/bgm.mp3")


async def test_slideshow_disabled_falls_back_to_images(monkeypatch):
    """开关关闭 → 回退逐张发图（旧行为）。"""
    from nonebot_plugin_parser.config import pconfig

    parser, downloads = _make_parser(monkeypatch, ffmpeg_ok=True)
    monkeypatch.setattr(pconfig, "parser_douyin_note_slideshow", False)

    result = await parser.parse_slides(_STATIC_NOTE_VID)

    assert len(result.img_contents) == 3
    assert result.video_contents == []
    assert downloads["audio"] == [], "逐张发图路径不应下载 BGM"


async def test_slideshow_ffmpeg_missing_falls_back_to_images(monkeypatch):
    """ffmpeg 不可用 → 回退逐张发图, 避免合成失败连图都丢。"""
    from nonebot_plugin_parser.config import pconfig

    parser, _ = _make_parser(monkeypatch, ffmpeg_ok=False)
    monkeypatch.setattr(pconfig, "parser_douyin_note_slideshow", True)

    result = await parser.parse_slides(_STATIC_NOTE_VID)

    assert len(result.img_contents) == 3
    assert result.video_contents == []


async def test_slideshow_no_bgm_falls_back_to_images(monkeypatch):
    """图文无 music.play_addr → 没有可合成的音频, 逐张发图。"""
    from nonebot_plugin_parser.config import pconfig

    payload = json.loads(json.dumps(_STATIC_NOTE_PAYLOAD))
    del payload["aweme_detail"]["music"]
    parser, _ = _make_parser(monkeypatch, ffmpeg_ok=True, payload=payload)
    monkeypatch.setattr(pconfig, "parser_douyin_note_slideshow", True)

    result = await parser.parse_slides(_STATIC_NOTE_VID)

    assert len(result.img_contents) == 3
    assert result.video_contents == []


async def test_compose_slideshow_partial_failures(monkeypatch):
    """降级链: 单图失败用剩余图, BGM 失败降级无声。"""
    from nonebot_plugin_parser import utils as utils_mod
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    async def _ok(name):
        return Path(f"/fake/{name}")

    async def _fail() -> Path:
        raise DownloadException("媒体下载失败")

    image_tasks = [
        asyncio.create_task(_ok("a.jpg")),
        asyncio.create_task(_fail()),
        asyncio.create_task(_ok("b.jpg")),
    ]
    audio_task = asyncio.create_task(_fail())

    captured = {}

    async def _fake_compose(image_paths, audio_path, output_path, **kwargs):
        captured["image_paths"] = list(image_paths)
        captured["audio_path"] = audio_path
        return Path("/fake/out.mp4")

    monkeypatch.setattr(utils_mod, "images_to_slideshow", _fake_compose)

    parser = DouyinParser()
    out = await parser._compose_slideshow(image_tasks, audio_task, Path("/fake/out.mp4"))

    assert out == Path("/fake/out.mp4")
    assert captured["image_paths"] == [Path("/fake/a.jpg"), Path("/fake/b.jpg")]
    assert captured["audio_path"] is None, "BGM 下载失败应降级为 None(无声)"


async def test_compose_slideshow_all_images_failed(monkeypatch):
    """全部图片下载失败 → DownloadException, 渲染层跳过该内容。"""
    from nonebot_plugin_parser import utils as utils_mod
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    async def _fail() -> Path:
        raise DownloadException("媒体下载失败")

    image_tasks = [asyncio.create_task(_fail()) for _ in range(3)]
    audio_task = asyncio.create_task(_fail())

    async def _unexpected(*args, **kwargs):
        raise AssertionError("全图失败不应走到合成")

    monkeypatch.setattr(utils_mod, "images_to_slideshow", _unexpected)

    parser = DouyinParser()
    with pytest.raises(DownloadException):
        await parser._compose_slideshow(image_tasks, audio_task, Path("/fake/out.mp4"))


async def test_compose_slideshow_conversion_timeout(monkeypatch):
    """转换段(ffmpeg 编码)超 video_send_timeout → DownloadException, 不无限阻塞消息。"""
    from nonebot_plugin_parser import utils as utils_mod
    from nonebot_plugin_parser.config import pconfig
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    monkeypatch.setattr(pconfig, "parser_video_send_timeout", 1)

    async def _slow(image_paths, audio_path, output_path, **kwargs):
        await asyncio.sleep(5)
        raise AssertionError("应被 wait_for 超时打断")

    monkeypatch.setattr(utils_mod, "images_to_slideshow", _slow)

    async def _ok() -> Path:
        return Path("/fake/a.jpg")

    parser = DouyinParser()
    with pytest.raises(DownloadException):
        await parser._compose_slideshow([asyncio.create_task(_ok())], asyncio.create_task(_ok()), Path("/fake/out.mp4"))


async def test_compose_slideshow_wraps_oserror(monkeypatch):
    """OSError(磁盘满/Windows 文件占用)也包成 DownloadException, 不打断整条消息渲染。"""
    from nonebot_plugin_parser import utils as utils_mod
    from nonebot_plugin_parser.parsers import DouyinParser
    from nonebot_plugin_parser.exception import DownloadException

    async def _boom(image_paths, audio_path, output_path, **kwargs):
        raise PermissionError("file in use")

    monkeypatch.setattr(utils_mod, "images_to_slideshow", _boom)

    async def _ok() -> Path:
        return Path("/fake/a.jpg")

    parser = DouyinParser()
    with pytest.raises(DownloadException):
        await parser._compose_slideshow([asyncio.create_task(_ok())], asyncio.create_task(_ok()), Path("/fake/out.mp4"))


async def test_slideshow_validates_output_duration(monkeypatch, tmp_path):
    """ffmpeg rc=0 但产物探不出时长(如输入静默失败) → RuntimeError, 半截文件不进缓存。"""
    from nonebot_plugin_parser.utils import media as media_mod
    from nonebot_plugin_parser.utils import images_to_slideshow

    async def _fake_exec(cmd):
        # 模拟 rc=0 且写出了残缺产物
        Path(cmd[-1]).write_bytes(b"")

    async def _fake_probe(path):
        return None

    async def _fake_size(path):
        return (400, 300)

    monkeypatch.setattr(media_mod, "exec_ffmpeg_cmd", _fake_exec)
    monkeypatch.setattr(media_mod, "probe_media_duration", _fake_probe)
    monkeypatch.setattr(media_mod, "_probe_image_size", _fake_size)

    img = tmp_path / "img.jpg"
    img.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError, match="时长异常"):
        await images_to_slideshow([img], None, out)
    assert not out.exists(), "半截产物不得 replace 进缓存"
    assert not list(tmp_path.glob("*_tmp.mp4")), "临时产物应被清理"


# ---------------------------------------------------------------------------
# 真实 ffmpeg 合成（仓库自带真实 BGM 样本 audio_sources/music_playurl.mp3,
# 时长 16.1175s: 2 图 → 每图 min(5, 8.06)=5s 循环快切, 总长 ≈16.1s=BGM）
# ---------------------------------------------------------------------------

ffmpeg_missing = which("ffmpeg") is None or which("ffprobe") is None

_REAL_BGM = Path(__file__).parent.parent.parent / "audio_sources" / "music_playurl.mp3"


@pytest.mark.skipif(ffmpeg_missing, reason="本机无 ffmpeg/ffprobe")
async def test_images_to_slideshow_real_ffmpeg(tmp_path):
    from PIL import Image

    from nonebot_plugin_parser.utils import (
        has_audio_stream,
        images_to_slideshow,
        probe_media_duration,
    )
    from nonebot_plugin_parser.utils.media import _probe_image_size

    # 两张不同尺寸的图 → 画布取 max(宽) x max(高) = 400x400, 小图黑边居中
    img1 = tmp_path / "img1.jpg"
    img2 = tmp_path / "img2.jpg"
    Image.new("RGB", (400, 300), "red").save(img1)
    Image.new("RGB", (300, 400), "blue").save(img2)

    out = tmp_path / "slideshow.mp4"
    result = await images_to_slideshow([img1, img2], _REAL_BGM, out)

    assert result == out
    assert out.exists()

    # 时长 ≈ BGM 16.12s (总长恒等于 BGM; 每图 min(5, 8.06)=5s 循环快切)
    duration = await probe_media_duration(out)
    assert duration is not None
    assert abs(duration - 16.1175) < 1.5, f"时长应 ≈16.1s, 实际 {duration}"

    # BGM 被合入
    assert await has_audio_stream(out), "合成视频应含音频轨"

    # 画布 = 最大宽 x 最大高, 偶数对齐
    assert await _probe_image_size(out) == (400, 400)


@pytest.mark.skipif(ffmpeg_missing, reason="本机无 ffmpeg/ffprobe")
async def test_images_to_slideshow_mixed_formats_all_present(tmp_path):
    """混编格式(jpeg+webp)轮播: 每张图都必须出现。

    回归 2026-09-29 线上事故: 旧实现用 concat demuxer, 它按首个文件选解码器,
    ffmpeg<6 上 webp 段被当 mjpeg 解码静默丢帧 → 3 图只出 2 图且首图被拉长。
    现实现每图独立输入各自解码, 任何版本 ffmpeg 都不会混。
    """
    from PIL import Image

    from nonebot_plugin_parser.utils import images_to_slideshow
    from nonebot_plugin_parser.utils.media import exec_ffmpeg_cmd, probe_media_duration

    per = 2.0
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    imgs = []
    for i, _ in enumerate(colors):
        p = tmp_path / f"img{i}.{'jpg' if i == 0 else 'webp'}"
        Image.new("RGB", (320, 240), colors[i]).save(p)
        imgs.append(p)

    out = tmp_path / "mixed.mp4"
    await images_to_slideshow(imgs, None, out, per_image=per)
    duration = await probe_media_duration(out)
    assert duration is not None
    assert abs(duration - 3 * per) < 0.5, f"时长应 ≈6s, 实际 {duration}"

    # 每段中点抽帧, 判定中心颜色与对应纯色图一致
    for i in range(3):
        frame = tmp_path / f"frame{i}.bmp"
        await exec_ffmpeg_cmd(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{i * per + per / 2:.2f}",
                "-i",
                str(out),
                "-frames:v",
                "1",
                str(frame),
            ]
        )
        assert frame.exists(), f"第 {i} 段中点抽不到帧"
        r, g, b = Image.open(frame).convert("RGB").getpixel((160, 120))
        er, eg, eb = colors[i]
        assert abs(r - er) < 60 and abs(g - eg) < 60 and abs(b - eb) < 60, (
            f"第 {i} 段应显示颜色 {colors[i]}, 实际 ({r}, {g}, {b})"
        )


@pytest.mark.skipif(ffmpeg_missing, reason="本机无 ffmpeg/ffprobe")
async def test_images_to_slideshow_bgm_not_truncated(tmp_path):
    """长 BGM 不被截断: 幻灯片总长恒等于 BGM, 图片循环快切。

    回归1: 旧实现 per_image = clamp(BGM/图数, 2, 8), 30s BGM + 2 图被截成
    16s 视频、音频丢一半。回归2: 均摊版每图 15s 太长。现实现每图
    min(5, 30/2)=5s 循环, 总长 30s, 序列 红蓝红蓝... 直到 BGM 结束。
    """
    from PIL import Image

    from nonebot_plugin_parser.utils import (
        images_to_slideshow,
        probe_media_duration,
    )
    from nonebot_plugin_parser.utils.media import exec_ffmpeg_cmd

    img1 = tmp_path / "img1.jpg"
    img2 = tmp_path / "img2.jpg"
    Image.new("RGB", (200, 200), "red").save(img1)
    Image.new("RGB", (200, 200), "blue").save(img2)
    bgm = tmp_path / "bgm.wav"
    await exec_ffmpeg_cmd(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30", str(bgm)])

    out = tmp_path / "long.mp4"
    await images_to_slideshow([img1, img2], bgm, out, per_image=5.0)

    duration = await probe_media_duration(out)
    assert duration is not None
    assert duration > 27, f"30s BGM 不应被截断, 实际总长 {duration}s (旧实现为 16s)"

    # 循环快切: 5s/图, 序列 红蓝红蓝...; 各段中点颜色应交替
    from PIL import Image

    expect = [(255, 0, 0), (0, 0, 255), (255, 0, 0)]
    for i, color in enumerate(expect):
        frame = tmp_path / f"cyc{i}.bmp"
        await exec_ffmpeg_cmd(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{i * 5 + 2.5:.2f}", "-i", str(out), "-frames:v", "1", str(frame)]
        )
        r, g, b = Image.open(frame).convert("RGB").getpixel((100, 100))
        er, eg, eb = color
        assert abs(r - er) < 60 and abs(g - eg) < 60 and abs(b - eb) < 60, (
            f"{i * 5 + 2.5}s 应显示 {color}, 实际 ({r}, {g}, {b})"
        )


@pytest.mark.skipif(ffmpeg_missing, reason="本机无 ffmpeg/ffprobe")
async def test_images_to_slideshow_no_audio(tmp_path):
    """无 BGM → 无声视频, 单遍轮播每图 per_image 秒（此处传 3s）。"""
    from PIL import Image

    from nonebot_plugin_parser.utils import (
        has_audio_stream,
        images_to_slideshow,
        probe_media_duration,
    )

    img = tmp_path / "img.jpg"
    Image.new("RGB", (200, 200), "green").save(img)

    out = tmp_path / "silent.mp4"
    await images_to_slideshow([img], None, out, per_image=3.0)

    duration = await probe_media_duration(out)
    assert duration is not None
    assert abs(duration - 3.0) < 1.0, f"单图无声应 3s, 实际 {duration}"
    assert not await has_audio_stream(out)


# ---------------------------------------------------------------------------
# 实况照片 BGM 合并: 静音 AAC 轨必须被替换 (2026-09-27 线上事故)
# 抖音实况照片 mp4 带一条 -91dB 全静音 AAC 轨, 旧逻辑 has_audio_stream
# 只查流存在性 → 误判"已含原声"跳过合并, 发出的视频全程无声。
# ---------------------------------------------------------------------------


@pytest.mark.skipif(ffmpeg_missing, reason="本机无 ffmpeg/ffprobe")
async def test_merge_bgm_replaces_silent_track(tmp_path):
    """静音 AAC 轨不算原声: 必须被 BGM 替换, 且 BGM 长于视频时截到视频长。"""
    from nonebot_plugin_parser.utils import media as media_mod
    from nonebot_plugin_parser.utils import (
        has_audio_stream,
        probe_media_duration,
        has_audible_audio_stream,
    )
    from nonebot_plugin_parser.parsers import DouyinParser

    # 3s 视频 + 全静音立体声 AAC (模拟实况照片)
    video = tmp_path / "live.mp4"
    await media_mod.exec_ffmpeg_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            str(video),
        ]
    )
    assert await has_audio_stream(video), "前置: 静音轨也是音频流"
    assert not await has_audible_audio_stream(video), "前置: 静音轨应判为不可闻"

    # 10s 可闻 BGM (440Hz 正弦, aac 编码避免构建缺 libmp3lame)
    audio = tmp_path / "bgm.m4a"
    await media_mod.exec_ffmpeg_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-c:a",
            "aac",
            str(audio),
        ]
    )

    async def _video() -> Path:
        return video

    async def _bgm() -> Path:
        return audio

    parser = DouyinParser()
    out = await parser._merge_bgm(asyncio.create_task(_video()), asyncio.create_task(_bgm()))

    assert out != video, "静音轨应被合并替换"
    assert out.exists()
    assert await has_audible_audio_stream(out), "合并产物应含可闻音频"
    duration = await probe_media_duration(out)
    assert duration is not None, "合并产物应探出时长"
    assert duration < 5.5, f"BGM(10s) 应被 -shortest 截到视频(3s)长, 实际 {duration}s"
    assert video.exists(), "下载缓存输入不应被删除 (cleanup_inputs=False)"
    assert audio.exists(), "下载缓存输入不应被删除 (cleanup_inputs=False)"

    # 重复解析(缓存命中)走产物快速路径, 不重跑 ffmpeg
    again = await parser._merge_bgm(asyncio.create_task(_video()), asyncio.create_task(_bgm()))
    assert again == out


@pytest.mark.skipif(ffmpeg_missing, reason="本机无 ffmpeg/ffprobe")
async def test_merge_bgm_keeps_audible_track(tmp_path):
    """可闻原声必须保留, 不被 BGM 覆盖。"""
    from nonebot_plugin_parser.utils import media as media_mod
    from nonebot_plugin_parser.utils import has_audible_audio_stream
    from nonebot_plugin_parser.parsers import DouyinParser

    # 3s 视频带 440Hz 可闻音轨 (模拟含原声的实况)
    video = tmp_path / "orig.mp4"
    await media_mod.exec_ffmpeg_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            str(video),
        ]
    )
    assert await has_audible_audio_stream(video)

    audio = tmp_path / "bgm.m4a"
    await media_mod.exec_ffmpeg_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=10",
            "-c:a",
            "aac",
            str(audio),
        ]
    )

    async def _video() -> Path:
        return video

    async def _bgm() -> Path:
        return audio

    parser = DouyinParser()
    out = await parser._merge_bgm(asyncio.create_task(_video()), asyncio.create_task(_bgm()))
    assert out == video, "可闻原声应跳过合并返回原视频"
    assert not (tmp_path / "orig_bgm.mp4").exists()
