"""JSON 持久化小工具：原子写 + 容错读。

插件有多处「内存 dict/set ↔ 磁盘 JSON」的小型持久化（平台开关、失败记录、
Telegram 白名单、各平台凭据），此前各自 ``write_text`` 直接覆写：进程崩溃时
可能截断文件，下次启动轻则丢数据、重则 JSON 解析失败炸掉插件加载。这里统一收敛：

- 写：先写同目录临时文件再 ``os.replace``（同一文件系统内原子），不会出现半截文件
- 读：文件不存在返回默认值；损坏 / 结构异常时告警并返回默认值，不炸 import
"""

import os
import json
from typing import Any
from pathlib import Path

from nonebot import logger


def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写 ``.<name>.tmp`` 再 ``os.replace`` 覆盖目标文件。

    Windows 下目标文件被 AV/同步盘占用时 ``os.replace`` 可能失败，
    finally 清理临时文件避免残留。
    """
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_json(path: Path, data: Any, *, ensure_ascii: bool = False, indent: int = 2) -> None:
    """原子写 JSON 文件（UTF-8）。"""
    atomic_write_text(path, json.dumps(data, ensure_ascii=ensure_ascii, indent=indent))


def load_json_or(path: Path, default: Any, *, context: str) -> Any:
    """容错读 JSON：不存在返回 ``default``；损坏时告警并返回 ``default``（不抛）。

    坏文件保留在磁盘上供排查，下次成功保存时才被覆盖。
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.warning(f"{context}: 读取 {path.name} 失败, 使用默认值 (原文件保留待排查): {e!r}")
        return default


class JsonValueStore:
    """单键 JSON 值存储（凭据、令牌等小状态）：原子写 + 容错读。

    文件格式: ``{"<key>": <value>, **extra}``。
    :meth:`load` 在文件缺失 / 损坏 / 值为空时返回 ``None``，由调用方决定兜底
    （降级匿名 / 回退环境变量等）。各平台特定的校验（如 cookie 内必须含某字段、
    过期检查）不属于本类职责，留在各平台凭据模块。
    """

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.key = key

    def save(self, value: str, **extra: Any) -> None:
        """写入值（覆盖既有文件），``extra`` 为附带元数据（如 updated_at）。"""
        atomic_write_json(self.path, {self.key: value, **extra})

    def load(self) -> str | None:
        """读取值；文件缺失/损坏/值为空字符串时返回 None。"""
        data = load_json_or(self.path, None, context=f"{self.path.name} 读取")
        if not isinstance(data, dict):
            return None
        value = data.get(self.key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def clear(self) -> bool:
        """删除存储文件，返回是否原存在。"""
        if self.path.exists():
            self.path.unlink()
            return True
        return False
