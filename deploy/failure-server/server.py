"""nonebot-plugin-parser 失败链接接收服务。

单文件 FastAPI 服务，SQLite 存储。仅监听 127.0.0.1，经 nginx 反代对外。

安全：
- Bearer API key 鉴权（所有端点）
- key 从环境变量 API_KEY 读，长度校验 ≥32
- pydantic 输入校验 + 字段长度上限
- 速率限制（内存令牌桶，每 IP）
- 非 root 运行
"""

import os
import time
import hashlib
import logging
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from collections import defaultdict

from fastapi import Query, Header, FastAPI, Request, HTTPException
from pydantic import Field, BaseModel
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("failure-server")

API_KEY = os.environ.get("API_KEY", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "failures.db"
RETENTION_DAYS = 90

# 速率限制：每 IP 每分钟最多 N 次 report
_RATE_LIMIT = 10
_RATE_WINDOW = 60
_rate_buckets: dict[str, list[float]] = defaultdict(list)

MIN_KEY_LEN = 32

app = FastAPI(title="parser-failure-server", docs_url=None, redoc_url=None, openapi_url=None)


# ── 启动检查 ──────────────────────────────────────────────────────


@app.on_event("startup")
def _startup_checks():
    if len(API_KEY) < MIN_KEY_LEN:
        raise RuntimeError(f"API_KEY 未设或长度 < {MIN_KEY_LEN}，拒绝启动")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _init_db()


# ── 鉴权 ─────────────────────────────────────────────────────────


def _check_auth(authorization: str | None = Header(None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    # 常量时间比较防时序攻击
    if not _consteq(token, API_KEY):
        raise HTTPException(403, "invalid api key")


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= ord(x) ^ ord(y)
    return r == 0


# ── 速率限制 ─────────────────────────────────────────────────────


def _check_rate(client_ip: str) -> None:
    now = time.time()
    bucket = _rate_buckets[client_ip]
    # 清理过期
    _rate_buckets[client_ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[client_ip]) >= _RATE_LIMIT:
        raise HTTPException(429, "rate limit exceeded")
    _rate_buckets[client_ip].append(now)


# ── SQLite ───────────────────────────────────────────────────────


@contextmanager
def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    with _get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash TEXT NOT NULL,            -- url 的 sha256，避免日志泄露 token
                url TEXT NOT NULL,                 -- 完整 url（仅存储，日志不打印）
                platform TEXT NOT NULL,
                error TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                retries INTEGER DEFAULT 0,
                first_seen INTEGER,
                last_seen INTEGER,
                received_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_received ON failures(received_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_url_hash ON failures(url_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_platform ON failures(platform)")


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ── 请求模型（输入校验） ─────────────────────────────────────────


class ReportPayload(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    platform: str = Field(..., min_length=1, max_length=64)
    error: str = Field(..., max_length=4096)
    first_seen: int | None = None
    last_seen: int | None = None
    count: int = Field(default=1, ge=1, le=1000000)
    retries: int = Field(default=0, ge=0, le=1000000)


# ── 端点 ─────────────────────────────────────────────────────────


@app.post("/api/report")
async def report(
    payload: ReportPayload,
    request: Request,
    authorization: str | None = Header(None),
):
    _check_auth(authorization)
    _check_rate(request.client.host if request.client else "unknown")
    now = int(time.time())
    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO failures
                (url_hash, url, platform, error, count, retries,
                 first_seen, last_seen, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _url_hash(payload.url),
                payload.url,
                payload.platform,
                payload.error,
                payload.count,
                payload.retries,
                payload.first_seen or now,
                payload.last_seen or now,
                now,
            ),
        )
    # 日志脱敏：只记 hash + platform，不记完整 url
    log.info("[report] platform=%s url_hash=%s", payload.platform, _url_hash(payload.url))
    return {"status": "ok"}


@app.get("/api/failures")
async def list_failures(
    request: Request,
    authorization: str | None = Header(None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    platform: str | None = Query(default=None),
):
    _check_auth(authorization)
    with _get_db() as conn:
        if platform:
            rows = conn.execute(
                "SELECT * FROM failures WHERE platform=? ORDER BY received_at DESC LIMIT ? OFFSET ?",
                (platform, limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM failures WHERE platform=?", (platform,)).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM failures ORDER BY received_at DESC LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(r) for r in rows],
    }


@app.get("/", response_class=HTMLResponse)
async def index(authorization: str | None = Header(None)):
    """简单 HTML 页面展示最近失败（需 key，浏览器用 ?token= 或 Header 插件）。"""
    _check_auth(authorization)
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT platform, error, count, retries, received_at FROM failures ORDER BY received_at DESC LIMIT 100"
        ).fetchall()
    rows_html = (
        "\n".join(
            f"<tr><td>{r['platform']}</td><td><pre>{_esc(r['error'])}</pre></td>"
            f"<td>{r['count']}</td><td>{r['retries']}</td>"
            f"<td>{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['received_at']))}</td></tr>"
            for r in rows
        )
        or "<tr><td colspan=5>(空)</td></tr>"
    )
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>parser failures</title>
<style>body{{font-family:system-ui;margin:2em}} table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px;text-align:left}} pre{{white-space:pre-wrap;max-width:600px;overflow:hidden}}
</style></head><body>
<h2>最近失败链接 (共展示 100 条)</h2>
<table><tr><th>平台</th><th>错误</th><th>次数</th><th>重试</th><th>接收时间</th></tr>
{rows_html}
</table></body></html>"""


@app.get("/health")
async def health():
    """健康检查（无需鉴权，仅返回 ok，供 docker healthcheck / 反代探测）。"""
    return {"status": "ok"}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── 数据保留清理（启动时执行一次） ───────────────────────────────


@app.on_event("startup")
def _cleanup_old():
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    with _get_db() as conn:
        n = conn.execute("DELETE FROM failures WHERE received_at < ?", (cutoff,)).rowcount
        if n:
            log.info("[cleanup] removed %s records older than %s days", n, RETENTION_DAYS)
