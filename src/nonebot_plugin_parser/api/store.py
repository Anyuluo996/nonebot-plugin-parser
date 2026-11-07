"""异步解析 API 的任务存储（SQLite）。

设计要点：
- stdlib ``sqlite3``（WAL + busy_timeout），**每次操作新建连接**（不跨线程共享连接，
  规避 ``check_same_thread`` 线程问题），经 ``asyncio.to_thread`` 包装为 async。
- ``result_json`` 里不存任何 URL / 签名 / 宿主路径 —— 媒体一律 ``media://{filename}``
  占位符；base_url 与签名由 routes 层在响应时拼接（现签），改配置不影响存量任务。
- ``staged_files`` 存 ``[{filename, size}]``，供媒体端点反查（不拼用户输入路径）、
  配额释放与签名使用；``staged_bytes`` 是其字节数合计（DELETE/TTL 清理释放配额用）。
- ``meta`` 表存 pid 做单进程软检测：NoneBot --reload / 多 worker 会让 stale 标记误伤
  其他进程的 running 任务，检测到不一致仅 warning（不做跨平台存活探测）。
"""

import os
import json
import time
import asyncio
import sqlite3
from typing import Any
from pathlib import Path

from nonebot import logger

from ..config import pconfig

_DB_PATH: Path = pconfig.data_dir / "api_tasks.db"

TASK_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
"""task_id 合法形态（token_urlsafe(16) ≈ 22 字符）。校验先行，任何拼接路径前必须过它。"""


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """行 dict 的 JSON 列反序列化（staged_files / result_json）。"""
    row["staged_files"] = json.loads(row.get("staged_files") or "[]")
    if (result := row.get("result_json")) is not None:
        try:
            row["result"] = json.loads(result)
        except (TypeError, ValueError) as e:
            logger.warning(f"api 任务 result_json 反序列化失败 task={row.get('task_id', '?')[:8]}…: {e!r}")
            row["result"] = None
    row.pop("result_json", None)
    return row


def _connect() -> "sqlite3.Connection":
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id      TEXT PRIMARY KEY,
                url          TEXT NOT NULL,
                url_norm     TEXT NOT NULL,
                platform     TEXT,
                status       TEXT NOT NULL,
                error_kind   TEXT,
                error        TEXT,
                result_json  TEXT,
                staged_files TEXT NOT NULL DEFAULT '[]',
                staged_bytes INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL,
                started_at   INTEGER,
                finished_at  INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_url_norm ON tasks(url_norm, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_cleanup ON tasks(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_finished ON tasks(finished_at)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


# ── 同步核心（在 to_thread 里执行，每处新建连接） ────────────────────


def _sync_create_task(task_id: str, url: str, url_norm: str, now: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, url, url_norm, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (task_id, url, url_norm, now),
        )


def _sync_get_task(task_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return _normalize_row(dict(row)) if row else None


def _sync_get_staged_files(task_id: str) -> list[dict[str, Any]] | None:
    with _connect() as conn:
        row = conn.execute("SELECT staged_files FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return json.loads(row[0] or "[]")


def _sync_find_active_by_url(url_norm: str) -> dict[str, Any] | None:
    """按规范化 URL 查 queued/running 任务（去重用，新建的在前）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE url_norm = ? AND status IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            (url_norm,),
        ).fetchone()
    return _normalize_row(dict(row)) if row else None


def _sync_count_active() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'running')").fetchone()
    return int(row[0])


def _sync_set_running_if_queued(task_id: str, platform: str, now: int) -> bool:
    """queued → running 的条件流转（写前复查：行被 DELETE 则返回 False，调用方放弃）。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'running', platform = ?, started_at = ? WHERE task_id = ? AND status = 'queued'",
            (platform, now, task_id),
        )
    return cur.rowcount > 0


def _sync_set_result(task_id: str, result_json: str, staged_files: str, staged_bytes: int, now: int) -> bool:
    """写入成功结果（同样条件于 running，防 DELETE 竞态写出孤儿结果）。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'succeeded', result_json = ?, staged_files = ?, staged_bytes = ?, "
            "finished_at = ? WHERE task_id = ? AND status = 'running'",
            (result_json, staged_files, staged_bytes, now, task_id),
        )
    return cur.rowcount > 0


def _sync_set_failed(task_id: str, error_kind: str, error: str | None, now: int) -> bool:
    """active 状态 → failed（失败流转永远来自 queued/running）。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'failed', error_kind = ?, error = ?, finished_at = ? "
            "WHERE task_id = ? AND status IN ('queued', 'running')",
            (error_kind, error, now, task_id),
        )
    return cur.rowcount > 0


def _sync_set_cancelled(task_id: str, now: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'cancelled', error_kind = 'cancelled', error = '任务已取消', "
            "finished_at = ? WHERE task_id = ? AND status IN ('queued', 'running')",
            (now, task_id),
        )
    return cur.rowcount > 0


def _sync_delete_task(task_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
    return cur.rowcount > 0


def _sync_list_tasks(limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    """任务列表（分页）。刻意不 SELECT result_json / staged_files 大列。"""
    with _connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        rows = conn.execute(
            "SELECT task_id, url, url_norm, platform, status, error_kind, error, "
            "staged_bytes, created_at, started_at, finished_at "
            "FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return total, [dict(r) for r in rows]


def _sync_cleanup_expired(ttl: int, now: int) -> list[dict[str, Any]]:
    """删除过期任务并返回其行（调用方取消任务 / 释放配额 / 删媒体目录）。

    active（queued/running，覆盖长期排队）按 created_at 过期；终态按 finished_at 过期。
    parse_with_retry 自带超时预算，running 不会永久滞留，TTL 只作兜底。
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT task_id, status, staged_files, staged_bytes FROM tasks "
            "WHERE (status IN ('queued', 'running') AND created_at < ?) "
            "OR (status NOT IN ('queued', 'running') AND finished_at < ?)",
            (now - ttl, now - ttl),
        ).fetchall()
        expired = [dict(r) for r in rows]
        if expired:
            marks = ",".join("?" for _ in expired)
            conn.execute(f"DELETE FROM tasks WHERE task_id IN ({marks})", [r["task_id"] for r in expired])
    return [_normalize_row(r) for r in expired]


def _sync_mark_stale_interrupted(now: int) -> int:
    """启动时把上一进程残留的 queued/running 标记为 failed/interrupted，返回标记条数。

    必须先于清理 job 首跑执行：先拿到 finished_at 才不会被清理误删成 404，
    轮询方拿到的是明确的失败而非未知的丢失。
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'failed', error_kind = 'interrupted', error = '服务重启导致任务中断', "
            "finished_at = ? WHERE status IN ('queued', 'running')",
            (now,),
        )
    if cur.rowcount:
        logger.warning(f"启动清理：{cur.rowcount} 个上进程残留的 API 任务已标记为 interrupted")
    return cur.rowcount


def _sync_known_task_ids() -> set[str]:
    with _connect() as conn:
        return {r[0] for r in conn.execute("SELECT task_id FROM tasks")}


def _sync_get_meta(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _sync_set_meta(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ── 异步门面 ─────────────────────────────────────────────────────────


async def init_db() -> None:
    await asyncio.to_thread(_init_db)


async def create_task(task_id: str, url: str, url_norm: str) -> None:

    await asyncio.to_thread(_sync_create_task, task_id, url, url_norm, int(time.time()))


async def get_task(task_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_sync_get_task, task_id)


async def get_staged_files(task_id: str) -> list[dict[str, Any]] | None:
    """只取 staged 清单（媒体端点反查用）：行不存在返回 None，避免高频命中的
    媒体请求为读一个文件名反序列化整份 MB 级 result_json。"""
    return await asyncio.to_thread(_sync_get_staged_files, task_id)


async def find_active_by_url(url_norm: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_sync_find_active_by_url, url_norm)


async def count_active() -> int:
    return await asyncio.to_thread(_sync_count_active)


async def set_running_if_queued(task_id: str, platform: str) -> bool:

    return await asyncio.to_thread(_sync_set_running_if_queued, task_id, platform, int(time.time()))


async def set_result(
    task_id: str, result: dict[str, Any], staged_files: list[dict[str, Any]], staged_bytes: int
) -> bool:

    return await asyncio.to_thread(
        _sync_set_result,
        task_id,
        json.dumps(result, ensure_ascii=False),
        json.dumps(staged_files, ensure_ascii=False),
        staged_bytes,
        int(time.time()),
    )


async def set_failed(task_id: str, error_kind: str, error: str | None = None) -> bool:

    return await asyncio.to_thread(_sync_set_failed, task_id, error_kind, error, int(time.time()))


async def set_cancelled(task_id: str) -> bool:

    return await asyncio.to_thread(_sync_set_cancelled, task_id, int(time.time()))


async def delete_task(task_id: str) -> bool:
    return await asyncio.to_thread(_sync_delete_task, task_id)


async def list_tasks(limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    return await asyncio.to_thread(_sync_list_tasks, limit, offset)


async def cleanup_expired() -> list[dict[str, Any]]:

    return await asyncio.to_thread(_sync_cleanup_expired, pconfig.api_task_ttl, int(time.time()))


async def mark_stale_interrupted() -> int:

    return await asyncio.to_thread(_sync_mark_stale_interrupted, int(time.time()))


async def known_task_ids() -> set[str]:
    return await asyncio.to_thread(_sync_known_task_ids)


async def check_single_process() -> None:
    """单进程软检测：meta 表记录上次进程 pid，不一致仅 warning。

    刻意不做跨平台存活探测（Windows 上 ``os.kill(pid, 0)`` 语义是 TerminateProcess，
    探测会误杀）。NoneBot 常规单进程部署不会触发；--reload / 多 worker 场景以此提醒。
    """

    current = os.getpid()
    prev = await asyncio.to_thread(_sync_get_meta, "pid")

    def _record() -> None:
        _sync_set_meta("pid", str(current))
        _sync_set_meta("started_at", str(int(time.time())))

    if prev is not None and prev != str(current):
        logger.warning(
            f"检测到不同的进程 pid（上次 {prev} / 本次 {current}）：API 任务存储被多进程共享时, "
            "stale 标记与配额计数会互相干扰。本功能仅支持单进程部署, 请勿开启 --reload 或多 worker。"
        )
    await asyncio.to_thread(_record)
