"""抖音 Web API ``x-secsdk-web-signature`` 风控签名 (ArgusSecurityPlugin)。

抖音对受保护接口 (含 PC detail 的 ``/aweme/v1/web/aweme/detail/``) 校验
该签名: 缺 ``uifid`` 报 ``403 Uifid Not Found``, 缺签名报 ``403 Signature
Not Found``; 签名不合法则可能静默返回 200 + 空 body。算法是四个输入的纯函数::

    signature = md5(f"{uifid}_{timestamp}_{SALT}_{canonical_query}")

``SALT`` 取自 secsdk VM 字符串表 (``runtime_bundler_34.js``, project-id 34),
与账号/会话无关。``uifid`` 是真实浏览器会话种下的 ``UIFID`` Cookie (320 位
十六进制), 本地算不出, 取一次可长期复用 (见 ``ttwid.get_effective_uifid``)。

算法实现改编自开源项目 (保留原始版权声明):

    Source:
        https://github.com/Evil0ctal/Douyin_TikTok_Download_API
        src/dtk/signing/native/websign.py (Apache License 2.0)
    该项目逆向了 runtime_bundler_34.js (@byted/secsdk-strategy v1.0.40) 的
    webSignUrl, 并于 2026-09-08 双重验证: 与真实浏览器产物逐字节比对一致,
    纯 Python 签名对 detail 等四个受保护接口实测 24/24 全部返回数据。

传输形态 (三处缺一不可, 字节必须一致):
1. query 内追加 ``uifid``/``timestamp`` 并以 ``&x-secsdk-web-signature=`` 结尾;
2. 同名三个 header: ``uifid`` / ``x-secsdk-web-signature`` / ``x-secsdk-web-expire``
   (expire 取值即签名的秒级时间戳);
3. 哈希预映像 = 最终发送的 query 去掉签名参数本身, 故 query 由本模块按
   URLSearchParams 规则 (仅 ``*-._`` 不转义) 编码一次生成, 调用方原样发送,
   不再走 httpx params dict (避免编码规则不一致破坏预映像)。
"""

from __future__ import annotations

import time
from hashlib import md5
from urllib.parse import quote
from collections.abc import Iterable

__all__ = [
    "EXPIRE_HEADER",
    "SALT",
    "SIGNATURE_PARAM",
    "UIFID_PARAM",
    "encode_pairs",
    "sign",
]

# douyin_web 项目的盐值 (secsdk VM 字符串表 #39)
SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"

SIGNATURE_PARAM = "x-secsdk-web-signature"
UIFID_PARAM = "uifid"
TIMESTAMP_PARAM = "timestamp"
EXPIRE_HEADER = "x-secsdk-web-expire"


def encode_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """按 secsdk 的 URLSearchParams 序列化规则编码键值对。

    ``*-._`` 是 JS URLSearchParams.toString() 唯一不转义的四字符, 其余
    (空格/非 ASCII) 一律 percent-encode 为 UTF-8。同一串既被哈希又被发送,
    编码规则错一处即签名失效。
    """
    return "&".join(f"{quote(k, safe='*-._')}={quote(v, safe='*-._')}" for k, v in pairs)


def sign(
    params: dict[str, str],
    uifid: str,
    *,
    timestamp: int | None = None,
) -> tuple[str, str, dict[str, str]]:
    """对请求参数计算 secsdk 网页签名。

    Args:
        params: 签名前进入 query 的全部键值对 (业务参数 + a_bogus), 值传
            **未编码** 原文, 由本模块统一编码一次。保持插入序
            (与真实浏览器一致的 a_bogus 在末尾)。
        uifid: 访客 ID, 签名绑定对象。已在 params 中时不重复追加。
        timestamp: 秒级时间戳; 缺省取当前时间, 测试可固定。

    Returns:
        ``(query, signature, headers)``: ``query`` 以
        ``&x-secsdk-web-signature=<sig>`` 结尾, 调用方拼到 URL 后原样发送;
        ``headers`` 为应附带的三个同名头。
    """
    stamp = str(int(time.time() if timestamp is None else timestamp))
    covered = list(params.items())
    if not any(name == UIFID_PARAM for name, _ in covered):
        covered.append((UIFID_PARAM, uifid))
    covered.append((TIMESTAMP_PARAM, stamp))
    query = encode_pairs(covered)
    signature = md5(f"{uifid}_{stamp}_{SALT}_{query}".encode()).hexdigest()
    headers = {UIFID_PARAM: uifid, SIGNATURE_PARAM: signature, EXPIRE_HEADER: stamp}
    return f"{query}&{SIGNATURE_PARAM}={signature}", signature, headers
