"""红果（番茄小说系）App 接口的 TTNet 轻量签名。

对 ``api5-normal-sinfonlineb.fqnovel.com`` 的 ``/novel/player/*`` 接口，服务端
校验 X-Khronos（秒级时间戳）+ X-Gorgon（对 query md5 前 4 字节、body md5 前
4 字节、固定魔数与时间戳做异或/循环移位混淆）+ X-SS-Req-Ticket（毫秒时间戳），
带 body 时还要求 X-SS-STUB 为 body 的 md5 大写十六进制。签名原文必须是**实际
发送**的 query 与 body 字节：先定稿 query/body，再签名，最后原样发出。

逆向参考: zhoufuweigg/guoapp ``native/core/provider_hongguo_sign.go``。
"""

import hashlib
from typing import Final

# 20 字节混淆密钥（guoapp 同款常量）
_SIGN_KEY: Final[bytes] = bytes(
    (
        0x44,
        0xB9,
        0xB9,
        0xD9,
        0xA4,
        0xAE,
        0xF9,
        0xFC,
        0xA4,
        0x93,
        0xAA,
        0x75,
        0x7C,
        0xA3,
        0xC2,
        0xC4,
        0xA4,
        0x96,
        0x93,
        0x8F,
    )
)

_PAYLOAD_LEN: Final[int] = 20

# Gorgon 头部魔数（版本标记 0x8404）
_GORGON_PREFIX: Final[bytes] = bytes((0x84, 0x04, 0x40, 0x1C, 0x00, 0x00))


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _rev8(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def sign_request(query: str, body: bytes | None, now: float) -> dict[str, str]:
    """生成红果 App 接口的签名 header。

    Args:
        query: 实际发送的 URL query **原文字节**对应的 str（含 `_rticket`）。
        body: 实际发送的请求体字节；GET / 无 body 传 None。
        now: 当前时间（秒，float）。

    Returns:
        需要附加的 header 字典（X-Khronos / X-Gorgon / X-SS-Req-Ticket，
        有 body 时含 X-SS-STUB）。
    """
    timestamp = int(now) & 0xFFFFFFFF
    payload = bytearray(_PAYLOAD_LEN)
    payload[0:4] = hashlib.md5(query.encode()).digest()[:4]

    headers: dict[str, str] = {}
    if body is not None:
        body_digest = hashlib.md5(body).digest()
        payload[4:8] = body_digest[:4]
        headers["X-SS-STUB"] = body_digest.hex().upper()

    payload[12:16] = b"\x00\x06\x0b\x1c"
    payload[16:20] = timestamp.to_bytes(4, "big")

    for index in range(_PAYLOAD_LEN):
        payload[index] ^= _SIGN_KEY[index]
    for index in range(_PAYLOAD_LEN):
        # 就地混淆：第 index 轮读取的 payload[(index+1)%20] 是仅异或密钥、
        # 尚未轮混淆的值（与参考实现对齐）
        mixed = _rotl8(payload[index], 4) ^ payload[(index + 1) % _PAYLOAD_LEN]
        payload[index] = _rev8(mixed) ^ 0xFF ^ _PAYLOAD_LEN

    headers["X-Khronos"] = str(timestamp)
    headers["X-Gorgon"] = (_GORGON_PREFIX + bytes(payload)).hex()
    headers["X-SS-Req-Ticket"] = str(int(now * 1000))
    return headers
