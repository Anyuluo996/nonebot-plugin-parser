"""QQ 音乐登录凭证持久化。

登录态（``Credential``）由 ``parqq登录`` 扫码指令获得，序列化到本地 JSON 文件，
下次启动及后续解析自动加载，无需重复扫码。

存储位置复用 ``nonebot_plugin_localstore`` 提供的插件数据目录（``config._data_dir``）。
"""

from __future__ import annotations

import json

from ...config import _data_dir

_CRED_FILE = _data_dir / "qqmusic_credential.json"

# musickey 默认有效期（秒），约 30 天；与 QQ 音乐 Web 端一致。
# 真实值优先取库返回的 key_expires_in，否则用此兜底。
_DEFAULT_KEY_EXPIRES_IN = 30 * 24 * 3600

# 持久化字段：登录态核心 + is_expired() 所需的两个时间字段。
# login_type / str_musicid / encrypt_uin 等辅助字段一并保存以便完整还原。
_PERSIST_FIELDS: tuple[str, ...] = (
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


def load_credential():
    """读取并返回 ``Credential``；文件不存在/已过期返回 None。"""
    from qqmusic_api import Credential

    if not _CRED_FILE.exists():
        return None
    try:
        data = json.loads(_CRED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("musickey"):
        return None
    try:
        cred = Credential(**{k: data.get(k) for k in _PERSIST_FIELDS if k in data})
    except Exception:
        return None
    # is_expired() 按 musickey_create_time + key_expires_in 判断，缺省值(0)会误判过期
    try:
        if cred.is_expired():
            return None
    except Exception:
        return None
    return cred


def save_credential(credential) -> None:
    """把登录态写入本地 JSON 文件。

    补全 ``is_expired()`` 依赖的时间字段：库在登录瞬间若未设置
    ``musickey_create_time``/``key_expires_in``，这里以当前时间 + 兜底有效期填入，
    避免下次加载时被误判为已过期。
    """
    import time

    data = {k: getattr(credential, k, "") for k in _PERSIST_FIELDS}
    if not data.get("musickey_create_time"):
        data["musickey_create_time"] = int(time.time())
    if not data.get("key_expires_in"):
        data["key_expires_in"] = _DEFAULT_KEY_EXPIRES_IN
    _CRED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_credential() -> bool:
    """删除本地凭证文件，返回是否原存在。"""
    if _CRED_FILE.exists():
        _CRED_FILE.unlink()
        return True
    return False


def is_available() -> bool:
    """是否存在可用（未过期）的登录态。"""
    return load_credential() is not None
