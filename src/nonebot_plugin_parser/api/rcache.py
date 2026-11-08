"""跨机解析结果缓存（Redis 共享索引 + 本机文件复用 + 跨机媒体拉取）。

一期仅接入 API 提交链路：``runner.submit_task`` 命中同链接（规范化后一致）时
直接复用上次的完整解析结果（不再解析、不再下载）；``runner._run`` 成功后写缓存。
聊天链路二期接入（从缓存还原 ``ParseResult`` 走现有渲染管线）。

信任边界（**谁控制 Redis 谁控制交付内容**）：条目按半可信输入处理——
五步流水线校验（前缀 → 受限解压 → JSON → HMAC 验签 → schema 强校验），
签名绑定键内 ``url_key``；对端媒体 URL 只由 ``parser_rcache_peer_map``
配置的 base_url 拼接（条目永不提供 URL 来源）；拉取关闭重定向、双重限长、
整体 deadline 包裹并发闸门。

密钥：``parser_rcache_shared_secret``（独立于 api_token——该 token 已发给
API 调用方，不得再掌握条目签名能力）。为空时用本机持久化 nonce 派生
（local-only 模式：只认本机条目），与共享模式同一套签名代码。

降级矩阵（全部只降级不报错）：redis 未装 / url 未配置 / instance_id 空 /
熔断期 → 功能不生效；payload 超限 / 降级产物 / 短时效内容 → 不缓存；
本机源缺失 / 对端拉取失败 / 配额不足 / deadline 超时 → 未命中走原解析。
"""

import re
import hmac
import json
import time
import zlib
import asyncio
import hashlib
import secrets
from typing import Any
from pathlib import Path

from nonebot import logger

from . import media, store
from ..config import pconfig
from ..persist import load_json_or, atomic_write_json

# ── 常量 ─────────────────────────────────────────────────────────────

_SCHEMA_VERSION = 1
_KEY_PREFIX = "pcache:v1:"
_INSTANCE_KEY_PREFIX = "pcache:inst:"
_MAX_ENTRY_BYTES = 1024 * 1024
"""条目原始 JSON 上限，超出跳过缓存（防大图文撑爆 Redis / 大值阻塞单线程）。"""
_MAX_DECOMPRESS = _MAX_ENTRY_BYTES + 1
"""受限解压上限：zlib 炸弹在解压阶段即被截断（校验在解压后，解压本身是攻击面）。"""
_COMPRESS_THRESHOLD = 512
"""原始字节超过此值才 zlib（小条目压缩收益为负）。"""
_MAX_STAGED_FILES = 500
_PULL_DEADLINE = 10.0
"""对端拉取整体 deadline（秒）：排队 + 拉取合计，超时按未命中走原解析。"""
_PULL_CONCURRENCY = 2
"""对端拉取并发闸门（与解析并发闸门独立，避免抢占正常解析的下载资源）。"""
_BREAKER_COOLDOWN = 30.0
"""Redis 异常后熔断时长：期间 get/put 直接 no-op，解析零感知。"""
_REDIS_OP_TIMEOUT = 0.5
"""单次 Redis 操作超时（网络分区时解析不被拖住）。"""
_HEARTBEAT_TTL = 700
"""实例心跳键 TTL（秒），大于 run_cleanup 的 10 分钟间隔。"""

_SIG_INFO = b"rcache-sig-v1"
_MEDIA_INFO = b"media-sig-v1"
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]{22}\.(?:jpg|jpeg|png|gif|webp|mp4|webm|mp3|m4a|wav|silk|bin)$")
"""转存文件名白名单：gen_filename 产出恒为 token_urlsafe(16)(22 字符)+白名单扩展名。
条目里的 filename 必须全匹配——路径穿越分子串在此即死。"""

# Lua CAS 回写：仅当当前值摘要与预期一致时 SET KEEPTTL。
# 传 sha1 摘要而非整条旧值（省带宽）；cur 为 nil（键已过期）时不复活——
# 破坏 created_at 寿命链，宁可丢一次回写。
_CAS_REWRITE_LUA = (
    "local cur = redis.call('GET', KEYS[1]) "
    "if not cur then return 0 end "
    "if redis.sha1hex(cur) ~= ARGV[1] then return 0 end "
    "redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL') return 1"
)

# 输出影响配置指纹清单：集中维护，新增影响解析产物形态的配置在此登记
_FINGERPRINT_FIELDS = ("parser_max_size", "parser_bili_video_codes", "parser_duration_maximum")

_pull_sem = asyncio.Semaphore(_PULL_CONCURRENCY)
_fail_until = 0.0
_client: Any = None
_sig_key_cache: bytes | None = None
_media_key_cache: bytes | None = None
_cfg_fp_cache: str | None = None
_heartbeat_nonce: str | None = None

metrics: dict[str, Any] = {
    "hit_local": 0,
    "hit_pull": 0,
    "miss": 0,
    "skip_breaker": 0,
    "error": 0,
    "pull_bytes": 0,
    "pull_seconds": 0.0,
}
"""命中观测计数（run_cleanup 汇总打点）：hit_local/hit_pull/miss/skip_breaker/error。"""


class _RedisDown(Exception):
    """Redis 不可用（熔断已触发 / 池满 / 连接超时）。调用方一律按未命中处理。"""


class _EntryForeign(Exception):
    """验签失败：非本信任域写入（外来/升级期新版本），不删键。"""


# ── 密钥与键 ─────────────────────────────────────────────────────────


def _nonce_file() -> Path:
    return pconfig.data_dir / "rcache_instance.json"


def _load_or_create_nonce() -> str:
    """本机持久化 nonce（一次生成跨重启复用）。

    双用途：local-only 模式的签名密钥源 + 实例心跳的同异判定——
    同机重启 nonce 不变（心跳不误报），异机同 id 各自生成必然不同（正确告警）。
    """
    data = load_json_or(_nonce_file(), None, context="结果缓存实例 nonce")
    if isinstance(data, dict) and isinstance(data.get("nonce"), str) and data["nonce"]:
        return data["nonce"]
    nonce = secrets.token_hex(32)
    atomic_write_json(_nonce_file(), {"nonce": nonce})
    return nonce


def _shared_secret() -> str:
    """签名密钥源：显式共享密钥优先；空则回落本机持久化 nonce（local-only）。"""
    if pconfig.rcache_shared_secret:
        return pconfig.rcache_shared_secret
    return _load_or_create_nonce()


def _sig_key() -> bytes:
    global _sig_key_cache
    if _sig_key_cache is None:
        _sig_key_cache = hashlib.sha256(
            hmac.new(_shared_secret().encode(), _SIG_INFO, hashlib.sha256).digest()
        ).digest()
    return _sig_key_cache


def _media_key() -> bytes:
    """跨机自签媒体 URL 的子密钥——与 routes._sign_key **完全同公式**
    （HMAC(共享密钥, "media-sig-v1") 直接作 key，无二次包装），否则对端
    校验 403。密钥源由 routes 侧统一：shared_secret 优先，回落 api_token。
    """
    global _media_key_cache
    if _media_key_cache is None:
        secret = pconfig.rcache_shared_secret or pconfig.api_token
        _media_key_cache = hmac.new(secret.encode(), _MEDIA_INFO, hashlib.sha256).digest()
    return _media_key_cache


def reset_keys() -> None:
    """密钥与客户端缓存失效（配置热更 / 测试隔离）。"""
    global _sig_key_cache, _media_key_cache, _cfg_fp_cache, _client, _heartbeat_nonce
    _sig_key_cache = None
    _media_key_cache = None
    _cfg_fp_cache = None
    _client = None
    _heartbeat_nonce = None


def _plugin_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("nonebot-plugin-parser")
    except PackageNotFoundError:
        return "unknown"


def cfg_fingerprint() -> str:
    """输出影响配置指纹（进键：不同配置/版本的主机互不踩键）。"""
    global _cfg_fp_cache
    if _cfg_fp_cache is None:
        parts = [str(getattr(pconfig, name)) for name in _FINGERPRINT_FIELDS]
        parts.append(_plugin_version())
        _cfg_fp_cache = hashlib.md5("|".join(parts).encode()).hexdigest()[:8]
    return _cfg_fp_cache


def url_key(url_norm: str) -> str:
    return hashlib.sha256(url_norm.encode()).hexdigest()[:32]


def make_key(url_norm: str) -> str:
    return f"{_KEY_PREFIX}{cfg_fingerprint()}:{url_key(url_norm)}"


def eff_ttl() -> int:
    """条目生效 TTL：不超过本机媒体任务的保留期（条目先于其引用的媒体过期）。"""
    return min(pconfig.rcache_ttl, pconfig.api_task_ttl - 600)


def validate_config() -> list[str]:
    """启动期配置校验，返回警告文案列表（由 api/__init__ 打 log）。"""
    warns: list[str] = []
    if pconfig.rcache_enabled and pconfig.rcache_redis_url and not pconfig.rcache_instance_id:
        warns.append("parser_rcache_redis_url 已配置但 parser_rcache_instance_id 为空，结果缓存不启用")
    if pconfig.rcache_instance_id and pconfig.rcache_instance_id in pconfig.rcache_peer_map:
        warns.append("parser_rcache_peer_map 中包含本机 instance_id，请检查配置")
    if eff_ttl() < 300:
        warns.append(
            f"结果缓存生效 TTL 仅 {eff_ttl()}s（min(rcache_ttl, api_task_ttl-600)），"
            "低于 300s 视为配置错误，结果缓存不启用"
        )
    return warns


# ── Redis 客户端与熔断 ───────────────────────────────────────────────


def _get_client() -> Any:
    """懒初始化 Redis 客户端；未配置/未启用返回 None（功能不生效）。"""
    global _client
    if not (pconfig.rcache_enabled and pconfig.rcache_redis_url and pconfig.rcache_instance_id):
        return None
    if _client is None:
        try:
            import redis.asyncio as aioredis
        except ImportError:
            logger.warning("结果缓存需要 redis 包（uv add nonebot-plugin-parser[rcache]），未安装，功能不启用")
            return None
        _client = aioredis.from_url(
            pconfig.rcache_redis_url,
            socket_connect_timeout=0.3,
            socket_timeout=0.5,
            max_connections=16,
        )
    return _client


def _breaker_active() -> bool:
    return time.monotonic() < _fail_until


def _trip_breaker() -> None:
    global _fail_until
    _fail_until = time.monotonic() + _BREAKER_COOLDOWN
    logger.warning(f"结果缓存 Redis 异常，{_BREAKER_COOLDOWN:.0f}s 内跳过（按未命中处理）")


async def _redis_op(op: Any) -> Any:
    """单次 Redis 操作包装：统一超时；池满按 miss 不触发熔断，连接类错误熔断。"""
    try:
        return await asyncio.wait_for(op(), _REDIS_OP_TIMEOUT)
    except asyncio.TimeoutError as e:
        _trip_breaker()
        metrics["error"] += 1
        raise _RedisDown from e
    except Exception as e:
        # redis-py 连接池满抛 ConnectionError("Too many connections")：
        # 瞬时资源问题，按未命中处理即可，不应把缓存判死 30 秒
        if "Too many connections" in str(e):
            metrics["miss"] += 1
            raise _RedisDown from e
        if type(e).__name__ in ("ConnectionError", "TimeoutError", "RedisError"):
            _trip_breaker()
            metrics["error"] += 1
            raise _RedisDown from e
        raise


# ── 条目编码与验签 ───────────────────────────────────────────────────


def _canonical(entry: dict[str, Any]) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sign_entry(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "sig"}
    return hmac.new(_sig_key(), _canonical(body), hashlib.sha256).hexdigest()[:32]


def _encode_entry(entry: dict[str, Any]) -> bytes | None:
    """签名 + 编码。原始 JSON 超过上限返回 None（超限检查在压缩前的原始长度上——
    zlib 对重复内容压缩率极高，压缩后判定会被绕过）。"""
    entry["sig"] = _sign_entry(entry)
    raw = _canonical(entry)
    if len(raw) > _MAX_ENTRY_BYTES:
        return None
    if len(raw) > _COMPRESS_THRESHOLD:
        return b"Z" + zlib.compress(raw)
    return b"R" + raw


def _decode_entry(blob: bytes) -> dict[str, Any] | None:
    """五步流水线前三步（前缀 → 受限解压 → JSON → 验签）。

    返回 None = 未验签垃圾（损坏/超限/异格式），调用方删键；
    抛 :class:`_EntryForeign` = 验签失败（外来/升级期新版本），调用方不删键。
    """
    # 1. 前缀
    if blob[:1] not in (b"R", b"Z"):
        return None
    payload = blob[1:]
    # 2. 受限解压：zlib 炸弹在此被截断（校验在解压后，解压本身是攻击面）
    if blob[:1] == b"Z":
        try:
            payload = zlib.decompressobj().decompress(payload, _MAX_DECOMPRESS)
        except zlib.error:
            return None
        if len(payload) > _MAX_ENTRY_BYTES:
            return None
    elif len(payload) > _MAX_ENTRY_BYTES:
        return None
    # 3. JSON
    try:
        entry = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(entry, dict):
        return None
    # 4. 立即验签（先于一切 schema 校验，确认同信任域）
    sig = entry.pop("sig", None)
    if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign_entry(entry)):
        raise _EntryForeign
    return entry


def _validate_entry(entry: dict[str, Any], key_url_key: str) -> bool:
    """五步流水线第 5 步：schema 强校验（调用方保证已验签且版本/指纹已先行分流）。"""
    if entry.get("url_key") != key_url_key:  # 绑定键：整条复制到他键即死
        return False
    host = entry.get("host")
    if not isinstance(host, str) or not host:
        return False
    origin = entry.get("origin_task_id")
    if not isinstance(origin, str) or re.fullmatch(store.TASK_ID_PATTERN, origin) is None:
        return False
    if not isinstance(entry.get("created_at"), int) or entry["created_at"] <= 0:
        return False
    staged = entry.get("staged_files")
    if not isinstance(staged, list) or len(staged) > _MAX_STAGED_FILES:
        return False
    total = 0
    for f in staged:
        if not isinstance(f, dict):
            return False
        name, size = f.get("filename"), f.get("size")
        if not isinstance(name, str) or _FILENAME_RE.fullmatch(name) is None:
            return False
        if not isinstance(size, int) or size < 0:
            return False
        total += size
    if total != entry.get("staged_bytes"):
        return False
    payload = entry.get("payload")
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("metadata"), dict)
        or not isinstance(payload.get("messages"), list)
    ):
        return False
    return True


def _entry_fresh(entry: dict[str, Any]) -> bool:
    """SETEX 续命防护：条目年龄不得超过生效 TTL。"""
    return time.time() - entry["created_at"] <= eff_ttl()


# ── 写入 ─────────────────────────────────────────────────────────────


async def put(
    url_norm: str,
    payload: dict[str, Any],
    staged: list[dict[str, Any]],
    staged_bytes: int,
    origin_task_id: str,
    keyword: str,
    *,
    degraded: bool = False,
) -> None:
    """解析成功后写缓存。整体吞异常：缓存只是加速，不能影响主链路。"""
    client = _get_client()
    if client is None or _breaker_active():
        return
    # 只缓存完整成功：降级产物（转存失败的媒体项）不进缓存
    if degraded:
        metrics["miss"] += 1
        return
    # 短时效内容（直播等）精确匹配 handler keyword，不做子串误伤
    if keyword in pconfig.rcache_skip_keywords:
        metrics["miss"] += 1
        return
    entry: dict[str, Any] = {
        "v": _SCHEMA_VERSION,
        "host": pconfig.rcache_instance_id,
        "origin_task_id": origin_task_id,
        "platform": "",
        "url_key": url_key(url_norm),
        "cfg_fp": cfg_fingerprint(),
        "created_at": int(time.time()),
        "payload": payload,
        "staged_files": staged,
        "staged_bytes": staged_bytes,
    }
    try:
        blob = _encode_entry(entry)
    except Exception:
        logger.debug("结果缓存条目编码失败（不影响解析）", exc_info=True)
        return
    if blob is None:
        metrics["miss"] += 1
        return
    try:
        await _redis_op(lambda: client.set(make_key(url_norm), blob, ex=eff_ttl()))
    except _RedisDown:
        return
    except Exception:
        logger.debug("结果缓存写入失败（不影响解析）", exc_info=True)


# ── 读取与复用 ───────────────────────────────────────────────────────


async def get(url_norm: str, new_task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], int] | None:
    """查询并落地缓存条目，返回 ``(payload, staged_files, staged_bytes)``。

    媒体文件已物化到本机 ``api_media/{new_task_id}/``（本机硬链复用或跨机
    拉取），占位符文件名保持不变——载荷可零改写写入新任务行。任何环节
    失败返回 None（未命中），调用方走原解析。
    """
    client = _get_client()
    if client is None:
        return None
    if _breaker_active():
        metrics["skip_breaker"] += 1
        return None
    key = make_key(url_norm)
    try:
        blob = await _redis_op(lambda: client.get(key))
    except _RedisDown:
        return None
    except Exception:
        metrics["error"] += 1
        logger.debug("结果缓存读取失败（按未命中）", exc_info=True)
        return None
    if not isinstance(blob, (bytes, bytearray)):
        metrics["miss"] += 1
        return None

    # 五步流水线
    try:
        entry = _decode_entry(bytes(blob))
    except _EntryForeign:
        metrics["miss"] += 1
        return None
    if entry is None:
        # 未验签垃圾（损坏/超限/异格式）：删键清场
        await _delete_quiet(client, key)
        metrics["miss"] += 1
        return None
    uk = url_key(url_norm)
    # 版本/配置指纹不匹配（升级期新版本条目、异配置主机写入）：未命中**不删键**，
    # 与验签失败同一待遇——否则旧版本滚动升级期间会互删新条目
    if entry.get("v") != _SCHEMA_VERSION or entry.get("cfg_fp") != cfg_fingerprint():
        metrics["miss"] += 1
        return None
    if not _validate_entry(entry, uk):
        # 已验签但结构违规：同信任域垃圾 → 删键
        await _delete_quiet(client, key)
        metrics["miss"] += 1
        return None
    if not _entry_fresh(entry):
        await _delete_quiet(client, key)
        metrics["miss"] += 1
        return None

    dest_dir = media.task_media_dir(new_task_id)
    if entry["host"] == pconfig.rcache_instance_id:
        return await _reuse_local(entry, dest_dir, client, key)
    peer_base = pconfig.rcache_peer_map.get(entry["host"])
    if peer_base is None:
        # 对端不在 peer_map（配置不全或 local-only 模式）：未命中不删键
        metrics["miss"] += 1
        return None
    return await _pull_peer(entry, peer_base, dest_dir, client, key, url_norm, new_task_id, bytes(blob))


async def _delete_quiet(client: Any, key: str) -> None:
    try:
        await _redis_op(lambda: client.delete(key))
    except Exception:
        pass


async def _reuse_local(
    entry: dict[str, Any], dest_dir: Path, client: Any, key: str
) -> tuple[dict[str, Any], list[dict[str, Any]], int] | None:
    """本机条目：从源任务目录硬链复用（占位符文件名不变）。

    源目录缺失（任务行已过期被清理）→ 删键 + 未命中（自愈，后续新解析覆盖）。
    与 ``run_cleanup`` 的竞态由 stage_path 的逐文件原子性兜底：任一文件失败
    整体回滚（remove_task_dir 幂等），绝不对半复用。
    """
    src_dir = media.task_media_dir(entry["origin_task_id"])
    staged_files: list[dict[str, Any]] = []
    try:
        for f in entry["staged_files"]:
            src = src_dir / f["filename"]
            if not src.is_file():
                await _delete_quiet(client, key)
                metrics["miss"] += 1
                return None
            await media.stage_path(src, dest_dir, filename=f["filename"])
            staged_files.append({"filename": f["filename"], "size": f["size"]})
    except media.MediaQuotaExceeded:
        await media.remove_task_dir(dest_dir.name)
        metrics["miss"] += 1
        return None
    except Exception:
        await media.remove_task_dir(dest_dir.name)
        await _delete_quiet(client, key)
        metrics["miss"] += 1
        return None
    metrics["hit_local"] += 1
    return entry["payload"], staged_files, entry["staged_bytes"]


async def _pull_peer(
    entry: dict[str, Any],
    peer_base: str,
    dest_dir: Path,
    client: Any,
    key: str,
    url_norm: str,
    new_task_id: str,
    orig_blob: bytes,
) -> tuple[dict[str, Any], list[dict[str, Any]], int] | None:
    """对端条目：经对端媒体端点把文件拉回本机再交付。

    - ``asyncio.wait_for`` 包在 ``_PULL_SEM`` **外**：排队时间计入整体预算
      （asyncio.timeout 为 3.11+，项目支持 3.10，统一用 wait_for）；
    - 进闸后重读条目：并发首个请求可能已拉完并回写为本机 → 直接本机复用；
    - 内层 ``except BaseException`` 统一回滚：deadline 超时（wait_for 取消内层
      协程）与异常都 ``remove_task_dir`` 扣回已转存文件的配额，不留孤儿目录；
    - 拉取成功后 CAS 回写条目为本机（二次命中走硬链，不再重复拉取）。
    """
    try:
        return await asyncio.wait_for(
            _do_pull(entry, peer_base, dest_dir, client, key, url_norm, new_task_id, orig_blob),
            _PULL_DEADLINE,
        )
    except asyncio.TimeoutError:
        metrics["miss"] += 1
        logger.info(f"结果缓存跨机拉取超时({_PULL_DEADLINE:.0f}s)，按未命中走原解析")
        return None
    except asyncio.CancelledError:
        # 外部取消（shutdown 等）：内层已回滚，原样上抛
        raise
    except _RedisDown:
        return None
    except Exception:
        # 内层已回滚；此处再兜底一次（remove_task_dir 幂等）
        await media.remove_task_dir(new_task_id)
        metrics["error"] += 1
        logger.opt(exception=True).debug("结果缓存跨机拉取失败（按未命中）")
        return None


async def _do_pull(
    entry: dict[str, Any],
    peer_base: str,
    dest_dir: Path,
    client: Any,
    key: str,
    url_norm: str,
    new_task_id: str,
    orig_blob: bytes,
) -> tuple[dict[str, Any], list[dict[str, Any]], int] | None:
    async with _pull_sem:
        # 闸门内重读：并发首个请求可能已回写为本机条目
        cas_blob = orig_blob
        try:
            blob = await _redis_op(lambda: client.get(key))
        except _RedisDown:
            return None
        if isinstance(blob, (bytes, bytearray)):
            try:
                fresh = _decode_entry(bytes(blob))
            except _EntryForeign:
                fresh = None
            if (
                fresh is not None
                and _validate_entry(fresh, url_key(url_norm))
                and fresh["host"] == pconfig.rcache_instance_id
                and fresh["origin_task_id"] != new_task_id
            ):
                return await _reuse_local(fresh, dest_dir, client, key)
            if fresh is not None and fresh["host"] == entry["host"]:
                # 对端条目仍在（可能 created_at 已更新）：以最新值为 CAS 基准
                entry, cas_blob = fresh, bytes(blob)

        started = time.monotonic()
        staged_files: list[dict[str, Any]] = []
        try:
            for f in entry["staged_files"]:
                url = _peer_media_url(peer_base, entry["origin_task_id"], f["filename"])
                await _pull_one(url, f["size"], dest_dir, f["filename"])
                staged_files.append({"filename": f["filename"], "size": f["size"]})
        except media.MediaQuotaExceeded:
            # 配额不足：整体回滚 → 未命中走原解析（绝不带残缺结果、绝不 500）
            await media.remove_task_dir(new_task_id)
            metrics["miss"] += 1
            return None
        except BaseException:
            # deadline 超时（wait_for 取消）/异常统一回滚：必须扣回已 stage
            # 文件占用的配额并清目录，不留孤儿（CancelledError 回滚后原样上抛）
            await media.remove_task_dir(new_task_id)
            raise

        # 拉取成功：CAS 回写为本机条目（KEEPTTL 保剩余寿命，不重新续命）
        entry["host"] = pconfig.rcache_instance_id
        entry["origin_task_id"] = new_task_id
        entry["created_at"] = int(time.time())
        new_blob = _encode_entry(entry)
        if new_blob is not None:
            await _cas_rewrite(client, key, cas_blob, new_blob)
        metrics["hit_pull"] += 1
        metrics["pull_bytes"] += entry["staged_bytes"]
        metrics["pull_seconds"] += time.monotonic() - started
        return entry["payload"], staged_files, entry["staged_bytes"]


async def _cas_rewrite(client: Any, key: str, old_blob: bytes, new_blob: bytes) -> None:
    """Lua CAS：仅当当前值与读取时一致才覆写（KEEPTTL）。

    防止覆盖期间新解析写入的更新条目；键已过期（cur=nil）不复活。
    传 sha1 摘要而非整条旧值（省带宽）。
    """
    old_digest = hashlib.sha1(old_blob).hexdigest()
    try:
        await _redis_op(lambda: client.eval(_CAS_REWRITE_LUA, 1, key, old_digest, new_blob))
    except _RedisDown:
        pass
    except Exception:
        logger.debug("结果缓存回写失败（不影响本次交付）", exc_info=True)


def _exp_span() -> int:
    """自签 URL 有效期：留 300s 跨机时钟余量，同时不超过配置窗口本身。

    下限 60s；api_media_sig_ttl 过小（≤360s）时余量偏薄，由启动 warning 提示 NTP。
    """
    ttl = pconfig.api_media_sig_ttl
    return min(max(ttl - 300, 60), ttl - 5)


def _peer_media_url(peer_base: str, origin_task_id: str, filename: str) -> str:
    """自签对端媒体 URL（对端用同一 shared_secret 派生的 media-sig-v1 校验）。"""
    exp = int(time.time()) + _exp_span()
    msg = f"{origin_task_id}/{filename}:{exp}".encode()
    sig = hmac.new(_media_key(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{peer_base}/parser/api/media/{origin_task_id}/{filename}?exp={exp}&sig={sig}"


async def _pull_one(url: str, declared_size: int, dest_dir: Path, filename: str) -> None:
    """从对端拉取单个媒体文件并按原文件名转存。

    - 不复用 ``DOWNLOADER.download_file``：走其 ``direct_client`` 直连（对端是
      内网基础设施，不走代理），文件名自管（不进 md5 缓存，exp 变化的 URL
      不产生缓存垃圾），并发不占下载闸门（独立 _PULL_SEM）；
    - 双重限长：Content-Length 与声明不符即拒；流式累计超过声明/配置上限即断；
    - 关闭重定向（对端被攻陷时的 302 不跟随，SSRF 面）；
    - 逐请求紧超时（不依赖外层 deadline，防单次握手吃光预算）；
    - 临时文件放插件 cache_dir（.part 后缀），finally 必清。
    """
    import httpx
    import aiofiles

    from ..download import DOWNLOADER

    cap = pconfig.parser_max_size * 1024 * 1024
    tmp = pconfig.cache_dir / f"rcache-pull-{secrets.token_hex(8)}.part"
    downloaded = 0
    try:
        async with DOWNLOADER.direct_client.stream(
            "GET",
            url,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0),
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"peer media HTTP {resp.status_code}")
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) != declared_size:
                raise RuntimeError(f"peer media content-length {cl} != declared {declared_size}")
            async with aiofiles.open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    downloaded += len(chunk)
                    if downloaded > declared_size or downloaded > cap:
                        raise RuntimeError("peer media exceeds declared size")
                    await f.write(chunk)
        if downloaded != declared_size:
            raise RuntimeError(f"peer media truncated: {downloaded} != {declared_size}")
        await media.stage_path(tmp, dest_dir, filename=filename)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ── 实例心跳 ─────────────────────────────────────────────────────────


async def heartbeat() -> None:
    """刷新实例心跳键（run_cleanup 周期调用）；首次调用检测 instance_id 撞车。

    心跳值携带持久化 nonce：同机重启 nonce 不变 → 不误报；异机同 id
    nonce 必然不同 → 正确告警。
    """
    global _heartbeat_nonce
    client = _get_client()
    if client is None or _breaker_active():
        return
    if _heartbeat_nonce is None:
        _heartbeat_nonce = _load_or_create_nonce()
        try:
            prev = await _redis_op(lambda: client.get(f"{_INSTANCE_KEY_PREFIX}{pconfig.rcache_instance_id}"))
            if isinstance(prev, (bytes, bytearray)):
                try:
                    prev_data = json.loads(bytes(prev))
                except ValueError:
                    prev_data = {}
                if prev_data.get("nonce") not in (None, _heartbeat_nonce):
                    logger.warning(
                        f"结果缓存心跳发现 instance_id={pconfig.rcache_instance_id!r} 已被其他实例持有"
                        "（nonce 不同），请检查各台配置是否重复——重复会把对方条目误判为本机"
                    )
        except _RedisDown:
            return
        except Exception:
            logger.debug("结果缓存心跳读取失败", exc_info=True)
    value = json.dumps({"nonce": _heartbeat_nonce, "ts": int(time.time())})
    try:
        await _redis_op(
            lambda: client.set(f"{_INSTANCE_KEY_PREFIX}{pconfig.rcache_instance_id}", value, ex=_HEARTBEAT_TTL)
        )
    except _RedisDown:
        return
    except Exception:
        logger.debug("结果缓存心跳写入失败", exc_info=True)


def metrics_summary() -> str:
    hits = metrics["hit_local"] + metrics["hit_pull"]
    if not (hits or metrics["miss"] or metrics["skip_breaker"] or metrics["error"]):
        return ""
    return (
        f"hit_local={metrics['hit_local']} hit_pull={metrics['hit_pull']} miss={metrics['miss']} "
        f"skip_breaker={metrics['skip_breaker']} error={metrics['error']} "
        f"pull={metrics['pull_bytes'] / 1024:.0f}KB/{metrics['pull_seconds']:.1f}s"
    )


def reset_metrics() -> None:
    for k in metrics:
        metrics[k] = 0
