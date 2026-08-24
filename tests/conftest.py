import os
from pathlib import Path

import pytest
import pytest_asyncio
from pytest_asyncio import is_async_test

if Path(".env.test").exists():
    os.environ["ENVIRONMENT"] = "test"
else:
    os.environ["ENVIRONMENT"] = "dev"

# htmlrender 0.8: provider 默认 None（无位图渲染），显式选 playwright 保持
# 与 0.7 默认行为一致。nonebot.init() 的 kwargs 在 env 文件存在时会被
# pydantic-settings 的 extra 校验丢弃，环境变量才是可靠注入路径。
os.environ.setdefault("RENDER__PROVIDER", "playwright")


def pytest_collection_modifyitems(items: list[pytest.Item]):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_nonebot():
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    # 初始化 NoneBot
    # htmlrender 0.8 起 provider 默认 None（无位图渲染能力），测试环境通过
    # RENDER__PROVIDER 环境变量显式选 playwright（见模块顶层 os.environ），
    # 与 0.7 默认行为一致；startup 默认 OFF = 首次渲染懒启动
    nonebot.init()

    # 加载适配器
    driver = nonebot.get_driver()
    driver.register_adapter(OnebotV11Adapter)

    # 加载插件
    nonebot.load_from_toml("pyproject.toml")


@pytest.fixture(scope="session", autouse=True)
def _test_media_files():
    """生成 tests/renders/test_forward_fallback.py 依赖的测试资源文件。

    这些文件 (_t_red.png / _t_green.png / _t_video.mp4) 自引入测试的 commit 起
    就未提交、也无 fixture 生成, 导致依赖视频的 3 个测试 FileNotFoundError,
    且 CI 的 "Test Render" job 同样失败。此处 session 级幂等生成。

    MP4 只需"存在且非空": UniHelper.video_seg() 仅 stat().st_size 判断大小后
    构造 Video(path=...) 存路径对象 (默认 use_base64=False 不 read_bytes),
    全程不解码、不调用 ffmpeg, 故写最小 ftyp box 头即可。
    """
    from PIL import Image

    test_dir = Path(__file__).parent
    files = {
        test_dir / "_t_red.png": ("RGB", (8, 8), (220, 40, 40)),
        test_dir / "_t_green.png": ("RGB", (8, 8), (40, 180, 60)),
    }
    for path, (mode, size, color) in files.items():
        if not path.exists():
            Image.new(mode, size, color=color).save(path)

    # 最小合法 MP4: ftyp box 头 (size=32, type='ftyp', brand='isom')。
    # 非空即可让 video_seg 的 stat().st_size > 0 判定通过。
    video = test_dir / "_t_video.mp4"
    if not video.exists():
        video.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41")
