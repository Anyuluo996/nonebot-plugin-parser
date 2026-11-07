"""异步解析 API 的 FastAPI 路由。

模块顶层**不 import fastapi** —— 由 :func:`build_router` 在 api/__init__.py 确认
依赖可用后懒导入构建，未安装 extras 时插件导入零影响。

响应约定：
- 存库的 result_json 含 ``media://{filename}`` 占位符；GET 任务时经
  :func:`_resolve_placeholders` 现签成完整 URL（``{base_url}/parser/api/media/...``），
  base_url 改配置不失效存量任务，签名每次刷新（晚取晚发不过期）。
- 错误文案在 runner 写库时已归一化（timeout/quota/cancelled/...），本层原样透传，
  不回显任何异常细节。
- 202 带 Location + Retry-After 头（Azure 异步请求-回复模式），轮询间隔建议 ≥ Retry-After。
"""

import re
import hmac
import time
from typing import TYPE_CHECKING, Any
from collections import defaultdict

from nonebot import logger

from . import media, store, runner
from ..config import pconfig

if TYPE_CHECKING:
    from fastapi import APIRouter

API_PREFIX = "/parser/api"

_TASK_ID_RE = re.compile(store.TASK_ID_PATTERN)
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|gif|webp|mp4|webm|mp3|m4a|wav|silk|bin)$")

# 鉴权失败按 IP 滑窗限流（查桶先于比对 token，被限的 IP 不再消耗比对）
_RATE_LIMIT = 10
_RATE_WINDOW = 60.0
_rate_buckets: dict[str, list[float]] = defaultdict(list)

# 签名子密钥（HMAC-SHA256(token, "media-sig-v1") 派生，与鉴权密钥职责分离：
# 泄露面不同、轮换语义一致 —— token 一换，全部已签发 URL 立即失效）
_sign_key_cache: bytes | None = None

_ENVELOPE_FIELDS = (
    "task_id",
    "status",
    "url",
    "platform",
    "source",
    "error_kind",
    "error",
    "created_at",
    "started_at",
    "finished_at",
)


def _sign_key() -> bytes:
    global _sign_key_cache
    if _sign_key_cache is None:
        _sign_key_cache = hmac.new(pconfig.api_token.encode(), b"media-sig-v1", "sha256").digest()
    return _sign_key_cache


def reset_sign_key() -> None:
    """签名子密钥缓存失效（测试/配置热更场景）。"""
    global _sign_key_cache
    _sign_key_cache = None


def _sign(task_id: str, filename: str, exp: int) -> str:
    message = f"{task_id}/{filename}:{exp}".encode()
    return hmac.new(_sign_key(), message, "sha256").hexdigest()[:32]


def _media_url(task_id: str, filename: str) -> str:
    exp = int(time.time()) + pconfig.api_media_sig_ttl
    sig = _sign(task_id, filename, exp)
    return f"{pconfig.api_base_url}{API_PREFIX}/media/{task_id}/{filename}?exp={exp}&sig={sig}"


def _resolve_placeholders(obj: Any, task_id: str) -> Any:
    """递归遍历载荷，把 ``media://`` 占位符替换为现签的完整媒体 URL。"""
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v, task_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_placeholders(v, task_id) for v in obj]
    if isinstance(obj, str) and obj.startswith("media://"):
        return _media_url(task_id, obj[len("media://") :])
    return obj


# ── 鉴权与限流 ───────────────────────────────────────────────────────


def _prune_bucket(bucket: list[float], now: float) -> list[float]:
    return [t for t in bucket if now - t < _RATE_WINDOW]


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    bucket = _prune_bucket(_rate_buckets[ip], now)
    _rate_buckets[ip] = bucket
    return len(bucket) >= _RATE_LIMIT


def _record_auth_fail(ip: str) -> None:
    _rate_buckets[ip].append(time.monotonic())


def prune_rate_buckets() -> None:
    """清掉过期桶（清理 job 调用），防公网扫描把字典撑爆。"""
    now = time.monotonic()
    for ip in list(_rate_buckets.keys()):
        pruned = _prune_bucket(_rate_buckets[ip], now)
        if pruned:
            _rate_buckets[ip] = pruned
        else:
            _rate_buckets.pop(ip, None)


def _check_auth(request: Any, authorization: str | None) -> None:
    from fastapi import HTTPException

    ip = request.client.host if request.client else "unknown"
    # 先查限流桶再比对 token：被限的 IP 不消耗比对
    if _rate_limited(ip):
        raise HTTPException(429, detail="too many auth failures", headers={"Retry-After": "60"})
    if not authorization or not authorization.startswith("Bearer "):
        _record_auth_fail(ip)
        raise HTTPException(401)
    token = authorization.removeprefix("Bearer ").strip()
    # 常量时间比较走 bytes：str 形态遇非 ASCII 输入会 TypeError（未捕获即 500，
    # 且异常发生在 _record_auth_fail 之前会绕过限流计数）；token 永不入日志
    if not token or not hmac.compare_digest(token.encode(), pconfig.api_token.encode()):
        _record_auth_fail(ip)
        # 只记长度与指纹前缀，便于区分多调用方又不泄密
        logger.debug(f"api 鉴权失败 ip={ip} token_len={len(token)} token_fp={token[:4]}…")
        raise HTTPException(403)


# ── 路由构建 ─────────────────────────────────────────────────────────


def build_router() -> "APIRouter":
    """构建并返回 APIRouter（仅在 fastapi 可用时由 api/__init__.py 调用）。"""
    from fastapi import Query, Header, Request, Response, APIRouter, HTTPException
    from pydantic import Field, BaseModel
    from fastapi.responses import FileResponse, JSONResponse

    router = APIRouter(prefix=API_PREFIX)

    class SubmitPayload(BaseModel):
        url: str = Field(..., min_length=1, max_length=2048)

    def _envelope(row: dict[str, Any], *, include_result: bool) -> dict[str, Any]:
        env = {k: row.get(k) for k in _ENVELOPE_FIELDS}
        if include_result:
            # 列表端点不携带 result 键（也不 SELECT 大列）；单查端点恒有 result 键（未完成为 null）
            payload = row.get("result")
            env["result"] = _resolve_placeholders(payload, row["task_id"]) if payload is not None else None
        return env

    @router.post("/tasks", status_code=202)
    async def create_task(
        payload: SubmitPayload, request: Request, authorization: str | None = Header(None)
    ) -> JSONResponse:
        _check_auth(request, authorization)
        url = payload.url.strip()
        if not url:
            raise HTTPException(422, detail="url is empty")

        # runner.submit_task 内部按 去重 → 零抓取校验 → 队列上限 的顺序判定
        try:
            row, _created = await runner.submit_task(url)
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e
        except runner.QueueFullError:
            raise HTTPException(429, detail="task queue is full", headers={"Retry-After": "30"}) from None

        task_id = row["task_id"]
        logger.info(f"api 任务已提交: task={task_id[:8]}… status={row['status']}")
        return JSONResponse(
            {"task_id": task_id, "status": row["status"]},
            status_code=202,
            headers={"Location": f"{API_PREFIX}/tasks/{task_id}", "Retry-After": "2"},
        )

    @router.get("/tasks")
    async def list_tasks(
        request: Request,
        authorization: str | None = Header(None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        url: str | None = Query(default=None, max_length=2048, description="按链接过滤（规范化后匹配）"),
    ) -> dict[str, Any]:
        _check_auth(request, authorization)
        # 按链接直查：规范化后精确匹配 url_norm（与提交去重同一键），查不到返回空列表而非 404
        url_norm = runner.normalize_url(url.strip()) if url and url.strip() else None
        total, rows = await store.list_tasks(limit, offset, url_norm)
        items = [_envelope(r, include_result=False) for r in rows]
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, request: Request, authorization: str | None = Header(None)) -> dict[str, Any]:
        _check_auth(request, authorization)
        if not _TASK_ID_RE.fullmatch(task_id):
            raise HTTPException(404, detail="task not found")
        row = await store.get_task(task_id)
        if row is None:
            raise HTTPException(404, detail="task not found")
        return _envelope(row, include_result=True)

    @router.delete("/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: str, request: Request, authorization: str | None = Header(None)) -> Response:
        _check_auth(request, authorization)
        if not _TASK_ID_RE.fullmatch(task_id):
            raise HTTPException(404, detail="task not found")
        row = await store.get_task(task_id)
        if row is None:
            raise HTTPException(404, detail="task not found")

        if row["status"] in ("queued", "running"):
            # 先取消并 await 执行体真正结束（与其清理串行），再写 cancelled + 清目录，
            # DELETE 返回后立刻重新提交同一 URL 不会与旧任务的收尾交错
            await runner.cancel_task(task_id)
            await store.set_cancelled(task_id)
        else:
            await store.delete_task(task_id)
        await media.remove_task_dir(task_id)  # 实测目录大小释放配额，幂等
        return Response(status_code=204)

    @router.get("/media/{task_id}/{filename}")
    async def get_media(task_id: str, filename: str, exp: int = Query(...), sig: str = Query(...)) -> FileResponse:
        # 三层穿越防护：task_id 格式 → 文件名格式 → DB staged 清单反查 + resolve 包含性兜底
        if not _TASK_ID_RE.fullmatch(task_id) or not _FILENAME_RE.fullmatch(filename):
            raise HTTPException(404, detail="not found")
        now = int(time.time())
        if exp < now:
            raise HTTPException(410, detail="signature expired")
        # bytes 比较，理由同 _check_auth（str 形态遇非 ASCII sig 抛 TypeError）
        if not hmac.compare_digest(_sign(task_id, filename, exp).encode(), sig.encode()):
            raise HTTPException(403, detail="invalid signature")
        # 专用查询只取 staged 清单：不反序列化整份 result_json（媒体端点被高频命中）
        staged_files = await store.get_staged_files(task_id)
        if staged_files is None:
            raise HTTPException(404, detail="not found")
        if filename not in {f.get("filename") for f in staged_files}:
            raise HTTPException(404, detail="not found")
        path = (media.task_media_dir(task_id) / filename).resolve()
        if not path.is_relative_to(media.MEDIA_ROOT.resolve()) or not path.is_file():
            raise HTTPException(404, detail="not found")
        mime = media.EXT_MIME.get(path.suffix.lower(), "application/octet-stream")
        # FileResponse 支持 Range 分片（协议端拉大视频需要 206）
        return FileResponse(path, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})

    return router
