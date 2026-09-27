"""QQ 音乐登录凭证持久化。

登录态（musickey / musicid / refresh_key 等）由 ``parqq登录`` 扫码指令写入本地
JSON，下次启动及后续解析自动加载。

存储位置复用 ``nonebot_plugin_localstore`` 提供的插件数据目录（``config._data_dir``）。

凭证同时被两条实现路径消费（字段名一致，共用同一份 JSON）：

- 增强引擎模式：构造引擎自己的 ``Credential``（含匿名凭证语义）
- 内置实现模式：构造 ``qqmusic_api.Credential``
"""

from __future__ import annotations

import json
import time
from typing import Any, cast
from collections.abc import Callable

from nonebot import logger

from ...config import _data_dir

_CRED_FILE = _data_dir / "qqmusic_credential.json"

#: musickey 默认有效期（秒），约 30 天；与 QQ 音乐 Web 端一致。
#: 真实值优先取库返回的 key_expires_in，否则用此兜底。
_DEFAULT_KEY_EXPIRES_IN = 30 * 24 * 3600

#: 内置实现模式的持久化字段：登录态核心 + is_expired() 所需的两个时间字段。
_BUILTIN_PERSIST_FIELDS: tuple[str, ...] = (
    "musicid",
    "musickey",
    "refresh_key",
    "refresh_token",
    "musickey_create_time",
    "key_expires_in",
    "str_musicid",
    "encrypt_uin",
    "login_type",
    "access_token",
    "openid",
    "unionid",
)


def _engine() -> Any:
    """取增强引擎模块；未安装返回 ``None``。"""
    from . import api

    return api.protocol


def _read_raw() -> dict[str, Any] | None:
    """读取原始 JSON；文件不存在/损坏返回 None。"""
    if not _CRED_FILE.exists():
        return None
    try:
        data = json.loads(_CRED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"QQ音乐凭证读取失败(JSON损坏),降级匿名: {e!r}")
        return None
    return data if isinstance(data, dict) else None


def _builtin_load_credential() -> Any:
    """内置模式：读 JSON 构造 ``qqmusic_api.Credential``；不可用返回 None。"""
    from qqmusic_api import Credential

    data = _read_raw()
    if not data or not data.get("musickey"):
        return None
    try:
        kwargs: dict[str, Any] = {k: data.get(k) for k in _BUILTIN_PERSIST_FIELDS if k in data}
        cred = Credential(**kwargs)
    except Exception as e:
        logger.warning(f"QQ音乐凭证字段缺失,无法构造 Credential: {e!r}")
        return None
    # is_expired() 按 musickey_create_time + key_expires_in 判断，缺省值(0)会误判过期
    try:
        if cred.is_expired():
            logger.info("QQ音乐凭证已过期,视为无登录态")
            return None
    except Exception as e:
        logger.warning(f"QQ音乐凭证过期检查异常,视为不可用: {e!r}")
        return None
    return cred


def load_credential_raw() -> Any:
    """返回**可用的**登录态。

    引擎模式：无可用登录态时返回匿名凭证（而非 None）。
    内置模式：无可用登录态返回 ``None``。

    传给取链层时必须已经是"能用"的状态 —— 过期凭证会让接口返回 104003，
    比匿名还差（匿名的表现是正常的 104003，过期的是各种诡异错误）。
    """
    protocol = _engine()
    if protocol is None:
        return _builtin_load_credential()

    data = _read_raw()
    if not data or not data.get("musickey"):
        return protocol.Credential.anonymous()

    try:
        cred = protocol.Credential.from_dict(data)
    except Exception as e:
        logger.warning(f"QQ音乐凭证字段缺失,无法构造 Credential: {e!r}")
        return protocol.Credential.anonymous()

    if cred.is_expired():
        logger.info("QQ音乐凭证已过期或缺少有效期字段,降级匿名")
        return protocol.Credential.anonymous()
    return cred


def load_credential() -> Any:
    """兼容旧调用点：等价于 :func:`load_credential_raw`。"""
    return load_credential_raw()


def _normalize(credential: Any) -> dict[str, Any]:
    """把任意形态的凭证对象转成可落盘的 dict（属性采集，两条路径通用）。"""
    if isinstance(credential, dict):
        return dict(credential)

    save_dict = getattr(credential, "save_dict", None)
    if callable(save_dict):
        save_dict = cast("Callable[[], dict[str, Any]]", save_dict)
        return dict(save_dict())

    fields = (
        "musicid",
        "musickey",
        "refresh_key",
        "refresh_token",
        "musickey_create_time",
        "key_expires_in",
        "str_musicid",
        "encrypt_uin",
        "login_type",
        "access_token",
        "openid",
        "unionid",
        "wxopenid",
        "qq",
        "nickname",
    )
    data = {k: getattr(credential, k, "") for k in fields if hasattr(credential, k)}
    if "nickname" not in data and hasattr(credential, "nick"):
        data["nickname"] = getattr(credential, "nick")
    return data


def save_credential(credential: Any) -> None:
    """把登录态写入本地 JSON 文件。

    补全过期判定所依赖的时间字段：扫码库在登录瞬间若未设置
    ``musickey_create_time`` / ``key_expires_in``，这里以当前时间 + 兜底有效期
    填入，避免下次加载时被误判为已过期。
    """
    data = _normalize(credential)
    if not data.get("musickey_create_time"):
        data["musickey_create_time"] = int(time.time())
    if not data.get("key_expires_in"):
        data["key_expires_in"] = _DEFAULT_KEY_EXPIRES_IN
    _CRED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_ck(raw: str) -> Any:
    """导入 ck 文本 / JSON，返回凭证对象。

    供 ``parqq导入ck`` 类指令用 —— 不用扫码也能建登录态。
    仅增强引擎模式支持；内置模式返回 ``None``。
    """
    protocol = _engine()
    if protocol is None:
        return None
    return protocol.parse_ck(raw)


def clear_credential() -> bool:
    """删除本地凭证文件，返回是否原存在。"""
    if _CRED_FILE.exists():
        _CRED_FILE.unlink()
        return True
    return False


def is_available() -> bool:
    """是否存在可用（未过期）的登录态。"""
    cred = load_credential_raw()
    if cred is None:
        return False
    if not getattr(cred, "logged_in", True):
        return False
    try:
        return not cred.is_expired()
    except Exception:
        return False
