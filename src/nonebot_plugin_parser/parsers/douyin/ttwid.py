"""抖音 ttwid 凭据持久化与读取。

ttwid 是抖音 PC web detail 接口的登录态凭据（配合 ``a_bogus`` 签名才能解析
实况照片视频）。本模块提供两条获取途径的统一入口：

1. **指令持久化**：SUPERUSER 通过 ``dyttwid <值>`` 指令写入，存为本地 JSON
   文件（优先级高，便于热更新无需重启）。
2. **环境变量兜底**：``.env`` 的 ``parser_douyin_ttwid``（优先级低，作为
   未使用指令前的兜底配置）。

存储位置复用 ``nonebot_plugin_localstore`` 的插件数据目录（``config._data_dir``），
与 QQ 音乐登录态（``qqmusic_credential.json``）同构。
"""

from __future__ import annotations

import json
import time

from ...config import _data_dir

_TTWID_FILE = _data_dir / "douyin_ttwid.json"


def save_ttwid(value: str) -> None:
    """把 ttwid 写入本地 JSON 文件（覆盖既有值）。

    Args:
        value: ttwid 字符串（登录态凭据，从浏览器登录抖音后复制）。
    """
    data = {"ttwid": value, "updated_at": int(time.time())}
    _TTWID_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ttwid() -> str | None:
    """读取指令持久化的 ttwid；文件不存在/损坏/空值返回 None。"""
    if not _TTWID_FILE.exists():
        return None
    try:
        data = json.loads(_TTWID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    ttwid = data.get("ttwid")
    if isinstance(ttwid, str) and ttwid.strip():
        return ttwid.strip()
    return None


def get_effective_ttwid() -> str | None:
    """返回当前生效的 ttwid：指令持久化优先，环境变量兜底。

    优先级链：
        1. ``dyttwid`` 指令写入的持久化文件（热更新，无需重启）
        2. ``parser_douyin_ttwid`` 环境变量（兜底）

    Returns:
        生效的 ttwid 字符串，两者皆无时返回 None。
    """
    # 1. 指令持久化优先
    if ttwid := load_ttwid():
        return ttwid
    # 2. 环境变量兜底
    from ...config import pconfig

    return pconfig.douyin_ttwid
