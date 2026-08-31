"""依赖约束回归测试。

背景: qqmusic-api-python 0.7.2 元数据只声明 typing-extensions>=4.12.2,
但其 core/client.py 需要 `from typing_extensions import sentinel`,
该符号自 typing_extensions 4.16.0 才存在 (4.12.2–4.15.0 逐版本探测均无)。
约束过松时 pip 判定"已满足"不自动升级, 装到 4.13–4.15 即触发
ImportError: cannot import name 'sentinel' (wo4 nb2 容器 2026-08-24 实际踩坑)。
"""

import re
from pathlib import Path
from importlib.metadata import version

from packaging.version import Version
from packaging.specifiers import SpecifierSet

# sentinel 符号的真实引入版本, 已逐版本下载 PyPI wheel 验证
_SENTINEL_FLOOR = Version("4.16.0")


def test_typing_extensions_sentinel_importable():
    """运行时守卫: typing_extensions 被降级到 4.15.x 及以下时立刻暴露, 而不是等到 qqmusic 解析崩。"""
    from typing_extensions import sentinel  # noqa: F401

    installed = Version(version("typing_extensions"))
    assert installed >= _SENTINEL_FLOOR, f"typing_extensions {installed} 无 sentinel, 需 >= {_SENTINEL_FLOOR}"


def test_qqmusic_api_importable():
    """当年的实际故障面: qqmusic_api 顶层 import 即崩 (正确导入名是 qqmusic_api 而非 qqmusic)。"""
    from qqmusic_api import Client  # noqa: F401


def test_pyproject_typing_extensions_floor():
    """声明守卫: pyproject 下界必须放行 4.16.0 且挡住 4.15.0 (曾误写 >=4.13.0, 等于没收紧)。"""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    match = re.search(r'"typing_extensions([^"]+)"', pyproject.read_text(encoding="utf-8"))
    assert match, "pyproject.toml dependencies 中未找到 typing_extensions 声明"
    spec = SpecifierSet(match.group(1))

    assert _SENTINEL_FLOOR in spec, f"约束 {spec} 未放行 sentinel 引入版本 {_SENTINEL_FLOOR}"
    assert Version("4.15.0") not in spec, f"约束 {spec} 仍放行无 sentinel 的 4.15.0"
    installed = Version(version("typing_extensions"))
    assert installed in spec, f"当前环境 typing_extensions {installed} 不满足声明 {spec}"
