"""网易云音乐登录凭证持久化。

登录态（``MUSIC_U`` 等 cookie）由 ``par网易云登录`` 扫码指令获得，序列化到本地
JSON 文件，下次启动及后续解析自动加载，无需重复扫码。

存储位置复用 ``nonebot_plugin_localstore`` 提供的插件数据目录（``config._data_dir``），
与 QQ 音乐凭证（``qqmusic_credential.json``）同目录。
"""

from __future__ import annotations

import json

from ...config import _data_dir

_CRED_FILE = _data_dir / "netease_credential.json"


def load_credential() -> str | None:
    """读取并返回完整 cookie 字符串；文件不存在返回 None。

    网易云登录态核心是 ``MUSIC_U``，无明确过期时间（服务端按 cookie 有效期判定），
    这里不做本地过期推断 —— 调用方请求失败时由网易云返回 401/301 自行兜底。
    """
    if not _CRED_FILE.exists():
        return None
    try:
        data = json.loads(_CRED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cookie = data.get("cookie") or ""
    if not cookie or "MUSIC_U" not in cookie:
        return None
    return cookie


def save_credential(cookie: str) -> None:
    """把完整 cookie 字符串写入本地 JSON 文件。"""
    _CRED_FILE.write_text(
        json.dumps({"cookie": cookie}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_credential() -> bool:
    """删除本地凭证文件，返回是否原存在。"""
    if _CRED_FILE.exists():
        _CRED_FILE.unlink()
        return True
    return False


def is_available() -> bool:
    """是否存在可用的登录态（cookie 含 MUSIC_U）。"""
    return load_credential() is not None
