"""解析失败链接本地记录。

解析硬失败时（ParseException/DownloadException/未预期错误），把链接、平台、
错误原因、时间记录到 data_dir/parse_failures.json，供维护者手动排查。

- 按 URL 去重：同链接重复失败只更新 last_seen + count，不堆叠
- 上限 MAX_FAILURES 条：超过时淘汰 last_seen 最旧的
- 仅本地记录，不重试、不上报
"""

import json
import time
from typing import Any
from pathlib import Path

from nonebot import logger

from .config import pconfig

_FAILURES_PATH: Path = pconfig.data_dir / "parse_failures.json"
MAX_FAILURES = 200


def _load_or_initialize() -> dict[str, dict[str, Any]]:
    """从磁盘加载失败记录；文件不存在或损坏则初始化为空。"""
    if not _FAILURES_PATH.exists():
        return {}
    try:
        data = json.loads(_FAILURES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning(f"parse_failures.json 结构异常（非 dict），已重置: {type(data)}")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取 parse_failures.json 失败，已重置: {e}")
    return {}


# 内存缓存：url -> 失败记录 dict。模块加载时从磁盘恢复。
_failures: dict[str, dict[str, Any]] = _load_or_initialize()


def _save() -> None:
    """把内存缓存刷盘（同步，失败记录频率低）。"""
    _FAILURES_PATH.write_text(json.dumps(_failures, ensure_ascii=False, indent=2), encoding="utf-8")


def record_failure(url: str, platform: str, error: str) -> None:
    """记录一条解析失败。

    URL 已存在则更新 last_seen + count+1；不存在则新增，超 MAX_FAILURES 淘汰最旧。
    记录本身失败只 log warning，不抛异常（不阻断主解析流程）。
    """
    try:
        now = int(time.time())
        if existing := _failures.get(url):
            existing["last_seen"] = now
            existing["count"] = int(existing.get("count", 1)) + 1
            existing["error"] = error  # 更新为最新错误信息
            existing["platform"] = platform
        else:
            _failures[url] = {
                "url": url,
                "platform": platform,
                "error": error,
                "first_seen": now,
                "last_seen": now,
                "count": 1,
            }
            # 超限淘汰 last_seen 最旧的
            if len(_failures) > MAX_FAILURES:
                oldest_url = min(_failures, key=lambda u: _failures[u].get("last_seen", 0))
                _failures.pop(oldest_url, None)
        _save()
    except Exception as e:
        logger.warning(f"记录解析失败到本地失败（不影响主流程）: {e}")


def get_failures() -> list[dict[str, Any]]:
    """读取所有失败记录（按 last_seen 倒序，最新的在前）。供未来命令/排查使用。"""
    return sorted(_failures.values(), key=lambda r: r.get("last_seen", 0), reverse=True)


def clear_failures() -> None:
    """清空所有失败记录。"""
    _failures.clear()
    _save()
