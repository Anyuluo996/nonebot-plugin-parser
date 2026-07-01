"""抖音凭据 (ttwid / 完整 cookie) 持久化与读取。

抖音 PC web detail 接口要求登录态凭据 + ``a_bogus`` 签名配套才能解析实况照片视频。
本模块提供两类凭据的持久化与统一入口:

**完整 Cookie** (推荐, 抗风控):
1. **指令持久化**: SUPERUSER 通过 ``dycookie <整条 cookie>`` 指令写入本地 JSON
   (优先级最高, 热更新无需重启)。
2. **环境变量兜底**: ``.env`` 的 ``parser_douyin_cookie``。

**仅 ttwid** (向后兼容, 易被间歇性风控):
1. **指令持久化**: ``dyttwid <值>`` 指令写入本地 JSON。
2. **环境变量兜底**: ``parser_douyin_ttwid``。

统一入口 :func:`get_effective_credential` 按 cookie → ttwid 优先级返回可直接用作
``Cookie`` 头的字符串 (cookie 原样, ttwid 包成 ``ttwid=<值>``)。
"""

from __future__ import annotations

import json
import time

from ...config import _data_dir

_TTWID_FILE = _data_dir / "douyin_ttwid.json"
_COOKIE_FILE = _data_dir / "douyin_cookie.json"


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


# === 完整 Cookie (推荐, 抗风控) ===


def save_cookie(value: str) -> None:
    """把完整 Cookie 写入本地 JSON 文件（覆盖既有值）。

    Args:
        value: 完整 Cookie 字符串（含 ``sessionid``/``sid_guard``/``ttwid`` 等,
            从浏览器 F12 → Network → www.douyin.com → Cookie 整行复制）。
    """
    data = {"cookie": value, "updated_at": int(time.time())}
    _COOKIE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cookie() -> str | None:
    """读取指令持久化的完整 cookie；文件不存在/损坏/空值返回 None。"""
    if not _COOKIE_FILE.exists():
        return None
    try:
        data = json.loads(_COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cookie = data.get("cookie")
    if isinstance(cookie, str) and cookie.strip():
        return cookie.strip()
    return None


def get_effective_cookie() -> str | None:
    """返回当前生效的完整 cookie：指令持久化优先，环境变量兜底。

    优先级链：
        1. ``dycookie`` 指令写入的持久化文件（热更新，无需重启）
        2. ``parser_douyin_cookie`` 环境变量（兜底）

    Returns:
        生效的完整 cookie 字符串，两者皆无时返回 None。
    """
    if cookie := load_cookie():
        return cookie
    from ...config import pconfig

    return pconfig.douyin_cookie


def get_effective_credential() -> str | None:
    """返回可直接用作 ``Cookie`` 头的凭据字符串（统一入口）。

    优先级链：
        1. 完整 cookie（``dycookie`` 指令持久化 > ``parser_douyin_cookie`` .env）
        2. 仅 ttwid（``dyttwid`` 指令持久化 > ``parser_douyin_ttwid`` .env），
           包成 ``ttwid=<值>`` 形式

    完整 cookie 抗风控能力远强于仅 ttwid（含 ``sessionid``/``sid_guard`` 等登录态字段），
    故优先返回 cookie；仅当 cookie 未配置时回退 ttwid。

    Returns:
        ``key=value; key=value`` 格式的 cookie 字符串，或 ``ttwid=<值>``，或 None。
    """
    if cookie := get_effective_cookie():
        return cookie
    if ttwid := get_effective_ttwid():
        return f"ttwid={ttwid}"
    return None
