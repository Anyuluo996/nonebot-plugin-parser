"""红果 App 接口的 TT 设备标识：注册、持久化与换新。

番茄小说系播放接口（``/novel/player/video_model/v1/``）要求请求携带一个
**注册过**的 TT 设备标识（device_id / install_id）；随机编造的数字会得到
HTTP 200 + 空 body。设备注册走 ``log.snssdk.com/service/2/device_register/``：
gzip 压缩的设备信息 JSON 经 ttEncrypt（AES-128-CBC，密钥由随机数 SHA512 与
固定异或表派生）整体加密后 POST，无需验证码，响应直接给出 device_id /
install_id。

注册成功后持久化到插件数据目录 ``hongguo_device.json``，进程重启复用；
接口侧设备被风控（返回空数据）时调用 :func:`refresh_device` 换新。

加密实现参考: lingbol088-spec/short-drama-downloader ``liushen/flurl``；
注册载荷形态同其 ``device_register.py``（aid=8662, app_name=novelread,
package=com.phoenix.read）。
"""

import gzip
import json
import time
import uuid
import random
import asyncio
import hashlib
import secrets
from typing import Any
from pathlib import Path

import httpx
from nonebot import logger
from cryptography.hazmat.primitives.ciphers import Cipher, modes, algorithms
from cryptography.hazmat.primitives.padding import PKCS7

from ...config import pconfig
from ...persist import load_json_or, atomic_write_json

# 红果（番茄小说系）App 形态常量。version 取 7.1.3.32/71332 与 7.3.5.32/73532
# 均实测可用；UA 与 query 参数保持一致即可。公开给 __init__ 复用（签名 query
# 与 UA 必须同源）。
VERSION_CODE = "73532"
VERSION_NAME = "7.3.5.32"
DEVICE_TYPE = "25053RT47C"
DEVICE_BRAND = "Redmi"
OS_API = "36"
OS_VERSION = "16"
RESOLUTION = "1280*2772"
DPI = "520"
CHANNEL = "update_64"

APP_UA = (
    f"com.phoenix.read/{VERSION_CODE} (Linux; U; Android {OS_VERSION}; zh_CN; {DEVICE_TYPE}; "
    "Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 "
    "QuicVersion:c67e9834 2025-09-08)"
)

_REGISTER_URL = "https://log.snssdk.com/service/2/device_register/"

_DEVICE_FILE = "hongguo_device.json"

_register_lock = asyncio.Lock()

# 换新冷却: 风控期的并发解析各自要求换新设备, 不设冷却会形成单 IP 注册风暴
_REFRESH_COOLDOWN = 60.0
_last_refresh_at = 0.0


# ---------------------------------------------------------------------------
# ttEncrypt：设备注册请求体加密
# ---------------------------------------------------------------------------


# SHA512(state) 后的固定异或表（TTNet t-encrypt 协议常量）
def _tt_seeds() -> bytes:
    key1 = (
        0x52,
        0x09,
        0x6A,
        0xD5,
        0x30,
        0x36,
        0xA5,
        0x38,
        0xBF,
        0x40,
        0xA3,
        0x9E,
        0x81,
        0xF3,
        0xD7,
        0xFB,
        0x7C,
        0xE3,
        0x39,
        0x82,
        0x9B,
        0x2F,
        0xFF,
        0x87,
        0x34,
        0x8E,
        0x43,
        0x44,
        0xC4,
        0xDE,
        0xE9,
        0xCB,
        0x54,
        0x7B,
        0x94,
        0x32,
        0xA6,
        0xC2,
        0x23,
        0x3D,
        0xEE,
        0x4C,
        0x95,
        0x0B,
        0x42,
        0xFA,
        0xC3,
        0x4E,
        0x08,
        0x2E,
        0xA1,
        0x66,
        0x28,
        0xD9,
        0x24,
        0xB2,
        0x76,
        0x5B,
        0xA2,
        0x49,
        0x6D,
        0x8B,
        0xD1,
        0x25,
    )
    key2 = (
        0x1F,
        0xDD,
        0xA8,
        0x33,
        0x88,
        0x07,
        0xC7,
        0x31,
        0xB1,
        0x12,
        0x10,
        0x59,
        0x27,
        0x80,
        0xEC,
        0x5F,
        0x60,
        0x51,
        0x7F,
        0xA9,
        0x19,
        0xB5,
        0x4A,
        0x0D,
        0x2D,
        0xE5,
        0x7A,
        0x9F,
        0x93,
        0xC9,
        0x9C,
        0xEF,
        0xA0,
        0xE0,
        0x3B,
        0x4D,
        0xAE,
        0x2A,
        0xF5,
        0xB0,
        0xC8,
        0xEB,
        0xBB,
        0x3C,
        0x83,
        0x53,
        0x99,
        0x61,
        0x17,
        0x2B,
        0x04,
        0x7E,
        0xBA,
        0x77,
        0xD6,
        0x26,
        0xE1,
        0x69,
        0x14,
        0x63,
        0x55,
        0x21,
        0x0C,
        0x7D,
    )
    return bytes(a ^ b for a, b in zip(key1, key2))


_TT_SEEDS = _tt_seeds()


def _tt_encrypt(payload: bytes) -> bytes:
    """TTNet t-encrypt：magic + 随机 32 字节 + AES-128-CBC(SHA512(payload)+payload)。

    密钥/IV = SHA512(SHA512(rand) + 固定异或表) 的前 32 字节。
    """
    rand = secrets.token_bytes(32)
    key_iv = hashlib.sha512(hashlib.sha512(rand).digest() + _TT_SEEDS).digest()
    plaintext = hashlib.sha512(payload).digest() + payload
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key_iv[:16]), modes.CBC(key_iv[16:32])).encryptor()
    return b"\x74\x63\x05\x10\x00\x00" + rand + encryptor.update(padded) + encryptor.finalize()


# ---------------------------------------------------------------------------
# 设备注册
# ---------------------------------------------------------------------------


def _random_hex(length: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=length))


def _build_register_payload() -> tuple[dict[str, str], bytes]:
    """构造注册 query 参数与加密后的请求体。"""
    cdid = str(uuid.uuid4())
    openudid = _random_hex(16)
    clientudid = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)

    query = {
        "tt_data": "a",
        "os_api": OS_API,
        "device_type": DEVICE_TYPE,
        "ssmix": "a",
        "manifest_version_code": VERSION_CODE,
        "dpi": DPI,
        "version_name": VERSION_NAME,
        "ts": str(now_ms // 1000),
        "cpu_support64": "true",
        "app_type": "normal",
        "appTheme": "light",
        "ac": "wifi",
        "host_abi": "arm64-v8a",
        "update_version_code": VERSION_CODE,
        "channel": CHANNEL,
        "_rticket": str(now_ms),
        "device_platform": "android",
        "version_code": VERSION_CODE,
        "cdid": cdid,
        "os": "android",
        "is_android_pad": "0",
        "openudid": openudid,
        "package": "com.phoenix.read",
        "resolution": RESOLUTION,
        "os_version": OS_VERSION,
        "language": "zh",
        "device_brand": DEVICE_BRAND,
        "need_personal_recommend": "1",
        "aid": "8662",
        "minor_status": "0",
        "app_name": "novelread",
        "mcc_mnc": "46007",
    }

    body = {
        "magic_tag": "ss_app_log",
        "header": {
            "display_name": "红果短剧",
            "update_version_code": VERSION_CODE,
            "manifest_version_code": VERSION_CODE,
            "aid": 8662,
            "channel": CHANNEL,
            "package": "com.phoenix.read",
            "app_version": VERSION_NAME,
            "version_code": VERSION_CODE,
            "sdk_version": "3.7.3-rc.53-douyin-bugfix",
            "sdk_target_version": 29,
            "os": "Android",
            "os_version": OS_VERSION,
            "os_api": OS_API,
            "device_model": DEVICE_TYPE,
            "device_brand": DEVICE_BRAND,
            "device_manufacturer": "Xiaomi",
            "device_category": "phone",
            "cpu_abi": "arm64-v8a",
            "release_build": str(uuid.uuid4()),
            "density_dpi": DPI,
            "display_density": "mdpi",
            "resolution": RESOLUTION.replace("*", "x"),
            "language": "zh",
            "mac": ":".join(_random_hex(2) for _ in range(6)),
            "timezone": 8,
            "access": "wifi",
            "not_request_sender": 0,
            "carrier": "CHINA MOBILE",
            "mcc_mnc": "46007",
            "rom": f"EMUI-{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=13))}",
            "rom_version": _random_hex(2),
            "sig_hash": _random_hex(32),
            "openudid": openudid,
            "clientudid": clientudid,
            "sim_serial_number": [],
            "region": "CN",
            "tz_name": "Asia/Shanghai",
            "tz_offset": 28800,
            "sim_region": "cn",
        },
        "_gen_time": now_ms,
    }
    gzipped = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
    return query, _tt_encrypt(gzipped)


async def register_device() -> tuple[str, str]:
    """向 TT 设备注册接口注册新设备，返回 (device_id, install_id)。"""
    query, body = _build_register_payload()
    headers = {
        "Content-Type": "application/octet-stream;tt-data=a",
        "Accept-Encoding": "gzip",
        "User-Agent": APP_UA,
        "sdk-version": "2",
        "x-tt-request-tag": "t=0;n=1",
        "x-ss-req-ticket": str(int(time.time() * 1000)),
    }
    async with httpx.AsyncClient(trust_env=False, timeout=15.0, verify=pconfig.verify_ssl) as client:
        resp = await client.post(_REGISTER_URL, params=query, content=body, headers=headers)
    resp.raise_for_status()
    data: Any = resp.json()
    if isinstance(data.get("data"), dict):
        data = data["data"]
    device_id = str(data.get("device_id_str") or data.get("device_id") or "")
    install_id = str(data.get("install_id_str") or data.get("install_id") or "")
    if not device_id.isdigit() or not install_id.isdigit():
        raise ValueError(f"设备注册响应缺少有效标识: {str(data)[:200]}")
    logger.info(f"hongguo 设备注册成功: device_id={device_id}")
    return device_id, install_id


# ---------------------------------------------------------------------------
# 设备获取 / 持久化 / 换新
# ---------------------------------------------------------------------------


def _device_file() -> Path:
    return pconfig.data_dir / _DEVICE_FILE


def _load_persisted() -> tuple[str, str] | None:
    data = load_json_or(_device_file(), {}, context="hongguo device")
    device_id = str(data.get("device_id") or "")
    install_id = str(data.get("install_id") or "")
    if device_id.isdigit() and install_id.isdigit():
        return device_id, install_id
    return None


def _persist(device_id: str, install_id: str) -> None:
    try:
        atomic_write_json(_device_file(), {"device_id": device_id, "install_id": install_id})
    except OSError as e:
        logger.warning(f"hongguo 设备标识持久化失败（不影响本次解析）: {e!r}")


async def get_device() -> tuple[str, str]:
    """取当前可用设备标识：配置覆盖 > 本地持久化 > 自动注册。

    配置仅当 device_id 与 install_id **同时**为纯数字时生效。
    """
    device_id, install_id = pconfig.hongguo_device_id, pconfig.hongguo_install_id
    if device_id is not None and install_id is not None:
        return device_id, install_id

    if persisted := _load_persisted():
        return persisted

    async with _register_lock:
        # 双重检查：并发协程在锁上等待时可能已有别的协程完成注册
        if persisted := _load_persisted():
            return persisted
        device_id, install_id = await register_device()
        _persist(device_id, install_id)
        return device_id, install_id


async def refresh_device() -> tuple[str, str]:
    """强制换新设备（当前设备被接口风控时调用），换新结果覆盖持久化。

    - 配置覆盖生效时不注册（注册结果会被 get_device 的配置优先级无视，纯空转）；
    - 冷却期（60s）内的并发换新复用刚注册的设备：单 IP 短时注册风暴
      反而更容易触发字节侧风控。
    """
    global _last_refresh_at
    device_id, install_id = pconfig.hongguo_device_id, pconfig.hongguo_install_id
    if device_id is not None and install_id is not None:
        logger.warning("hongguo 设备为配置覆盖（parser_hongguo_device_id/install_id），跳过自动换新")
        return device_id, install_id

    async with _register_lock:
        now = time.monotonic()
        if now - _last_refresh_at < _REFRESH_COOLDOWN and (persisted := _load_persisted()):
            return persisted
        device_id, install_id = await register_device()
        _persist(device_id, install_id)
        _last_refresh_at = time.monotonic()
        return device_id, install_id
