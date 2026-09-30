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

**uifid** (可选增强, x-secsdk-web-signature 风控签名绑定对象):
1. **指令持久化**: ``dyuifid <值>`` 指令写入本地 JSON。
2. **环境变量兜底**: ``parser_douyin_uifid``。
3. **从完整 cookie 自动提取**: cookie 内含 ``UIFID`` 字段时直接使用。

统一入口 :func:`get_effective_credential` 按 cookie → ttwid 优先级返回可直接用作
``Cookie`` 头的字符串 (cookie 原样, ttwid 包成 ``ttwid=<值>``);
:func:`get_effective_uifid` 按上述三级返回访客 ID。
"""

from __future__ import annotations

import time

from ...config import _data_dir
from ...persist import JsonValueStore

_TTWID_STORE = JsonValueStore(_data_dir / "douyin_ttwid.json", "ttwid")
_COOKIE_STORE = JsonValueStore(_data_dir / "douyin_cookie.json", "cookie")
_UIFID_STORE = JsonValueStore(_data_dir / "douyin_uifid.json", "uifid")

# UIFID cookie 在浏览器里的各种拼写 (secsdk 按序取第一个非空, 大小写不敏感等效)
_UIFID_COOKIE_NAMES = ("uifid", "uifid_temp", "uifidtemp")


def save_ttwid(value: str) -> None:
    """把 ttwid 写入本地 JSON 文件（覆盖既有值）。

    Args:
        value: ttwid 字符串（登录态凭据，从浏览器登录抖音后复制）。
    """
    _TTWID_STORE.save(value, updated_at=int(time.time()))


def load_ttwid() -> str | None:
    """读取指令持久化的 ttwid；文件不存在/损坏/空值返回 None。"""
    return _TTWID_STORE.load()


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
    _COOKIE_STORE.save(value, updated_at=int(time.time()))


def load_cookie() -> str | None:
    """读取指令持久化的完整 cookie；文件不存在/损坏/空值返回 None。"""
    return _COOKIE_STORE.load()


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


# === uifid (x-secsdk-web-signature 风控签名的绑定对象) ===


def save_uifid(value: str) -> None:
    """把 uifid 写入本地 JSON 文件（覆盖既有值）。

    Args:
        value: UIFID 字符串（浏览器访问 www.douyin.com 后种下的 320 位
            十六进制访客身份 Cookie）。
    """
    _UIFID_STORE.save(value, updated_at=int(time.time()))


def load_uifid() -> str | None:
    """读取指令持久化的 uifid；文件不存在/损坏/空值返回 None。"""
    return _UIFID_STORE.load()


def uifid_from_cookie(cookie: str) -> str | None:
    """从完整 cookie 字符串中提取 UIFID 值；不含 UIFID 字段时返回 None。

    兼容 ``uifid``/``UIFID``/``uifid_temp`` 等拼写（取第一个非空）。
    """
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() in _UIFID_COOKIE_NAMES and value.strip():
            return value.strip()
    return None


def get_effective_uifid() -> str | None:
    """返回当前生效的 uifid：指令持久化 > 环境变量 > 完整 cookie 内自动提取。

    优先级链：
        1. ``dyuifid`` 指令写入的持久化文件（热更新，无需重启）
        2. ``parser_douyin_uifid`` 环境变量（兜底）
        3. 完整 cookie（``get_effective_cookie``）内的 UIFID 字段——与登录态
           同源最真实，显式配置优先是因为可能刻意指定另一会话的访客 ID

    Returns:
        生效的 uifid 字符串，三级均为空时返回 None。
    """
    if uifid := load_uifid():
        return uifid
    from ...config import pconfig

    if uifid := pconfig.douyin_uifid:
        return uifid
    if cookie := get_effective_cookie():
        return uifid_from_cookie(cookie)
    return None
