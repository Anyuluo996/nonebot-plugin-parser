# ILLU 请求签名（移植自 parser-lite，原作者感谢焦化葱葱等逆向贡献者）
import time
import random
import hashlib


def get_nonce() -> str:
    """生成 16 位随机流水号（去除易混淆字符）。"""
    chars = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
    return "".join(random.choice(chars) for _ in range(16))


def get_safe_sign(path: str, timestamp: str, noncestr: str) -> str:
    raw_str = f"{path}{timestamp}350704{noncestr}"
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


def sign_header(path: str) -> dict:
    timestamp = str(int(time.time()))
    nonce = get_nonce()
    return {
        "X-Bmob-SDK-Type": "API",
        "X-Bmob-Safe-Timestamp": timestamp,
        "X-Bmob-Noncestr-Key": nonce,
        "X-Bmob-Safe-Sign": get_safe_sign(path, timestamp, nonce),
        "X-Bmob-Secret-Key": "d2b11e2f977ed97c",
    }
