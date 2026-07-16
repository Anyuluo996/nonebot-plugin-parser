"""nonebot-plugin-parser 失败链接接收服务。

单文件 FastAPI 服务，SQLite 存储。仅监听 127.0.0.1，经 nginx 反代对外。

安全模型：
- POST /api/report 公开（无需 key），靠多重校验防滥用：
  · URL 域名白名单（必须来自已知平台）
  · platform 必须在已知枚举集合
  · 速率限制（5 次/分钟/IP）
  · 请求体大小上限 + 字段长度上限
  · 服务端去重（同 url_hash 只更新一行）
- GET /api/failures、GET /（HTML）需 Bearer key（防他人读数据）
- key 从环境变量 API_KEY 读，长度校验 ≥32
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
from urllib.parse import urlparse

from fastapi import Query, Header, FastAPI, Request, HTTPException
from pydantic import Field, BaseModel, field_validator
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("failure-server")

API_KEY = os.environ.get("API_KEY", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "failures.db"
RETENTION_DAYS = 90

# 速率限制：每 IP 每分钟最多 N 次 report（公开端点，收紧）
_RATE_LIMIT = 5
_RATE_WINDOW = 60
_rate_buckets: dict[str, list[float]] = defaultdict(list)

MIN_KEY_LEN = 32

# 已知平台域名白名单（POST 上报的 url 必须命中其一，子域也算）
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        # bilibili
        "bilibili.com",
        "b23.tv",
        "biligame.com",
        # douyin
        "douyin.com",
        "iesdouyin.com",
        # nga
        "nga.178.com",
        "ngabbs.com",
        "bbs.nga.cn",
        "nga.cn",
        # weibo
        "weibo.com",
        "weibo.cn",
        # xiaohongshu
        "xiaohongshu.com",
        "xhslink.com",
        # kuaishou
        "kuaishou.com",
        "chenzhongtech.com",
        # twitter / x
        "twitter.com",
        "x.com",
        # acfun
        "acfun.cn",
        # youtube / tiktok
        "youtube.com",
        "youtu.be",
        "tiktok.com",
        # telegram
        "t.me",
        "telegram.me",
        # zhihu
        "zhihu.com",
        # tieba
        "tieba.baidu.com",
        # 其他平台
        "lofter.com",
        "coolapk.com",
        "hupu.com",
        "heybox.com",
        "buff.163.com",
        "duitang.com",
        "music.163.com",
        "kugou.com",
        "pixiv.net",
        "pixivision.net",
    }
)

# 已知 platform 值（POST 上报的 platform 必须命中其一）
ALLOWED_PLATFORMS: frozenset[str] = frozenset(
    {
        "acfun",
        "bilibili",
        "buff",
        "coolapk",
        "douyin",
        "duitang",
        "heybox",
        "hupu",
        "illu",
        "kuaishou",
        "kugou",
        "lofter",
        "nga",
        "netease",
        "pixiv",
        "qsmusic",
        "qqmusic",
        "telegram",
        "tieba",
        "tiktok",
        "twitter",
        "weibo",
        "xiaohongshu",
        "youtube",
        "zhihu",
    }
)

app = FastAPI(title="parser-failure-server", docs_url=None, redoc_url=None, openapi_url=None)


# ── 启动检查 ──────────────────────────────────────────────────────


@app.on_event("startup")
def _startup_checks():
    if len(API_KEY) < MIN_KEY_LEN:
        raise RuntimeError(f"API_KEY 未设或长度 < {MIN_KEY_LEN}，拒绝启动")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _init_db()


# ── 鉴权（仅 GET 端点用） ────────────────────────────────────────


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


def _is_allowed_domain(url: str) -> bool:
    """url 的 host 命中白名单（精确或为白名单域名的子域）即放行。"""
    try:
        host = urlparse(url).hostname or ""
    except (ValueError, TypeError):
        return False
    host = host.lower()
    if not host:
        return False
    for d in ALLOWED_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return False


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
                url_hash TEXT NOT NULL UNIQUE,        -- UNIQUE：同 url 去重
                url TEXT NOT NULL,
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
        # 兼容旧表（无 UNIQUE）：尝试加约束，已存在则忽略
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_url_hash ON failures(url_hash)")
        except sqlite3.OperationalError:
            pass


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

    @field_validator("platform")
    @classmethod
    def _platform_must_be_known(cls, v: str) -> str:
        if v not in ALLOWED_PLATFORMS:
            raise ValueError(f"unknown platform: {v}")
        return v

    @field_validator("url")
    @classmethod
    def _url_must_be_whitelisted(cls, v: str) -> str:
        # 实例方法无法访问 frozenset，校验放端点里（需 _is_allowed_domain）
        return v


# ── 端点 ─────────────────────────────────────────────────────────


@app.post("/api/report")
async def report(payload: ReportPayload, request: Request):
    """公开上报端点（无需 key）。靠白名单/枚举/限流/去重防滥用。"""
    # 1. URL 域名白名单
    if not _is_allowed_domain(payload.url):
        raise HTTPException(403, "url domain not allowed")
    # 2. 速率限制
    _check_rate(request.client.host if request.client else "unknown")
    now = int(time.time())
    uh = _url_hash(payload.url)
    with _get_db() as conn:
        # 3. 去重：同 url_hash 存在则更新，不存在则插入
        conn.execute(
            """
            INSERT INTO failures
                (url_hash, url, platform, error, count, retries,
                 first_seen, last_seen, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_hash) DO UPDATE SET
                error=excluded.error,
                platform=excluded.platform,
                count=excluded.count,
                retries=excluded.retries,
                last_seen=excluded.last_seen,
                received_at=excluded.received_at
            """,
            (
                uh,
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
    log.info("[report] platform=%s url_hash=%s", payload.platform, uh)
    return {"status": "ok"}


@app.get("/api/failures")
async def list_failures(
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
    """简单 HTML 页面展示最近失败（需 key）。"""
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
