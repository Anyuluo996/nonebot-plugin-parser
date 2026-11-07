import os
from pathlib import Path

import pytest
import pytest_asyncio
from pytest_asyncio import is_async_test

if Path(".env.test").exists():
    os.environ["ENVIRONMENT"] = "test"
else:
    os.environ["ENVIRONMENT"] = "dev"

# 强制 fastapi driver: nonebug 插件的会话级 lifespan (nonebug_init autouse fixture)
# 经 FastAPI lifespan 跑真实 startup→shutdown, 需要 server_app; dev 依赖
# nonebot2[fastapi] 保证可用。OS 环境变量优先级高于 .env.test 文件配置。
os.environ["DRIVER"] = "~fastapi"

# 启动冒烟（防 2026-11-08 部署翻车类回归: startup hook 参数错误只在真实启动时爆炸,
# 单测隔离反而成了盲区）—— 以环境变量让 API 全开, 会话 lifespan 即每个测试会话
# 都跑一遍完整 API 注册链（建库/stale 标记/配额重建/include_router/清理 job 注册）,
# 任何 startup hook 回归会在会话 setup 阶段当场炸出。setdefault: 本地 .env.test
# 若已显式配置则以文件为准。
os.environ.setdefault("PARSER_API_ENABLED", "true")
os.environ.setdefault("PARSER_API_TOKEN", "startup-smoke-token-0123456789abcdef")
os.environ.setdefault("PARSER_API_BASE_URL", "http://startup-smoke.test")


def pytest_collection_modifyitems(items: list[pytest.Item]):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)

    # 测试分级: tests/parsers/ 下为真实网络测试(受平台风控影响, 风控时转 skip,
    # CI 绿不等于解析可用), 自动打 network marker, 可用 `-m "not network"` 跳过;
    # tests/others 与 tests/renders 为纯单元层(不依赖网络)。
    network_marker = pytest.mark.network
    for item in items:
        if item.path.parent.name == "parsers":
            item.add_marker(network_marker)


@pytest.fixture(scope="session")
def _nonebot_init():
    """覆盖 nonebug 插件的同名 fixture：插件加载并入其依赖链。

    nonebug 的 ``nonebug_init``（会话级 autouse）依赖 ``_nonebot_init`` 并在其后
    启动真实 FastAPI lifespan（startup hooks 全周期）。若用插件自带版本（只
    ``nonebot.init()``），lifespan 会跑在插件加载之前 —— hooks 注册表为空，所有
    startup hooks 都不会执行（2026-11-08 建设启动冒烟测试时实测定位）。
    """
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    # 初始化 NoneBot（含强制 DRIVER=~fastapi 等 conftest 环境变量）
    nonebot.init()

    # 加载适配器
    driver = nonebot.get_driver()
    driver.register_adapter(OnebotV11Adapter)

    # 加载插件（必须在 nonebug 会话 lifespan 之前完成，startup hooks 才会注册）
    nonebot.load_from_toml("pyproject.toml")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_nonebot(_nonebot_init):
    """保留旧 autouse 入口（部分用例隐式依赖「会话内插件已加载」），实际逻辑在 ``_nonebot_init``。"""


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
