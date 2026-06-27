# 小黑盒请求签名（移植自 parser-lite，参考 zhiyu1998/rconsole-plugin 方案优化 nonce）
# 严禁非法滥用或用于渗透测试

import time as _time
import random
import hashlib
import itertools
from typing import Final

BASE_URL: Final[str] = "api.xiaoheihe.cn"
PATH: Final[str] = "/bbs/app/link/tree"
SALT: Final[str] = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"


def get_nonce(time: int) -> str:
    """nonce 使用 timestamp + 随机数，避免确定性被风控（参考 zhiyu1998 方案）。"""
    raw = f"{time}{random.random()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _vm(e: int) -> int:
    return ((e << 1) & 0xFF) ^ 27 if (e & 0x80) else ((e << 1) & 0xFF)


def _qm(e: int) -> int:
    return _vm(e) ^ e


def _mm(e: int) -> int:
    return _qm(_vm(e))


def _ym(e: int) -> int:
    return _mm(_qm(_vm(e)))


def _gm(e: int) -> int:
    return _ym(e) ^ _mm(e) ^ _qm(e)


def _km(e: list[int]) -> list[int]:
    t0 = _gm(e[0]) ^ _ym(e[1]) ^ _mm(e[2]) ^ _qm(e[3])
    t1 = _qm(e[0]) ^ _gm(e[1]) ^ _ym(e[2]) ^ _mm(e[3])
    t2 = _mm(e[0]) ^ _qm(e[1]) ^ _gm(e[2]) ^ _ym(e[3])
    t3 = _ym(e[0]) ^ _mm(e[1]) ^ _qm(e[2]) ^ _gm(e[3])
    e[0], e[1], e[2], e[3] = t0, t1, t2, t3
    return e


def _av(e: str, t: str, n: int) -> str:
    i = t[:n]
    if not i:
        return ""
    res_chars: list[str] = []
    for ch in e:
        idx = ord(ch) % len(i)
        res_chars.append(i[idx])
    return "".join(res_chars)


def _sv(e: str, t: str) -> str:
    if not t:
        return ""
    res_chars: list[str] = [t[ord(ch) % len(t)] for ch in e]
    return "".join(res_chars)


def _interleave_js(arr: list[str]) -> str:
    if not arr:
        return ""
    max_len = max(len(s) for s in arr)
    out: list[str] = [
        s[i] for i, s in itertools.product(range(max_len), arr) if i < len(s)
    ]
    return "".join(out)


def get_hkey(time: int, nonce: str | None = None) -> str:
    e = PATH
    t = time + 1
    n = nonce if nonce is not None else get_nonce(time)

    parts = [seg for seg in e.split("/") if seg]
    e_norm = "/" + "/".join(parts) + "/"

    r = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"

    i_str = _interleave_js(
        [
            _av(str(t), r, -2),
            _sv(e_norm, r),
            _sv(n, r),
        ]
    )[:20]

    o = hashlib.md5(i_str.encode("utf-8")).hexdigest()

    last6 = o[-6:]
    arr = [ord(ch) for ch in last6]
    mixed = _km(arr)
    total = sum(mixed)
    a_val = total % 100
    a = f"{a_val:02d}"

    s = _av(o[:5], r, -4)
    return f"{s}{a}"


def build_url(link_id: str) -> str:
    """构造带签名的请求 URL。

    关键：nonce 只算一次，复用给 URL 参数和 hkey 签名，否则签名校验失败（非法请求）。
    """
    time = int(_time.time())
    nonce = get_nonce(time)
    return (
        f"https://{BASE_URL}{PATH}"
        "?os_type=web&app=heybox&client_type=web&version=999.0.4"
        f"&_time={time}&nonce={nonce}&hkey={get_hkey(time, nonce)}&link_id={link_id}"
        "&page=1&index=1&limit=5&x_client_type=weboutapp&x_app=heybox_website&x_os_type=Windows"
        "&web_version=2.5"
    )
