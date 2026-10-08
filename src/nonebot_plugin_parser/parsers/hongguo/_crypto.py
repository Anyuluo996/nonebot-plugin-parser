"""红果 CENC 加密流的媒体密钥推导。

App 播放接口返回的 ``video_list`` 变体带 ``encrypt_info.spade_a``：一段
Base64 混淆数据，尾部 tag 标注密钥版本，载荷是异或/位运算混淆过的 32 字节
hex 字符串，解码后即 AES-128 内容密钥（CENC cenc-aes-ctr 的
``tenc.default_kid`` 对应密钥），供 ffmpeg ``-decryption_key`` 解密。

逆向参考: zhoufuweigg/guoapp ``provider_hongguo_playback.go:hongguoContentKey``
与 lingbol088-spec/short-drama-downloader ``derive_content_key``（两实现等价）。
"""

import base64
import binascii


def derive_content_key(spade_a: str) -> bytes:
    """从 ``spade_a`` Base64 串推导 16 字节 AES 内容密钥。

    Raises:
        ValueError: 数据结构无效 / 密钥版本暂不支持 / 不是有效 AES-128 密钥。
    """
    text = spade_a.strip()
    padding = -len(text) % 4
    raw = base64.b64decode(text + "=" * padding)
    if len(raw) < 3:
        raise ValueError(f"spade_a 过短: {len(raw)} 字节")

    # 尾部 tag 长度 = 首三字节异或值 - 48
    tag_length = (raw[0] ^ raw[1] ^ raw[2]) - 48
    content_length = len(raw) - tag_length - 1
    if tag_length < 1 or content_length < 33 or content_length >= len(raw):
        raise ValueError(f"spade_a 结构无效 (tag_len={tag_length}, content_len={content_length})")

    seed = raw[len(raw) - tag_length - 2] ^ raw[len(raw) - tag_length - 1]
    tag = bytes(b ^ seed for b in raw[len(raw) - tag_length :])
    if tag in (b"app_v2", b"web_v2"):
        raise ValueError(f"spade_a 密钥版本暂不支持: {tag.decode(errors='replace')}")

    decoded = bytearray(content_length)
    # 初始值与混淆轮转按 guoapp；与 short-drama-downloader 实现仅影响首字节
    # (填充标记)，密钥本体 decoded[1:33] 两种实现一致。
    prev_even, prev_odd = 250, 85
    for index, current in enumerate(raw[1 : 1 + content_length]):
        if index % 2 == 0:
            previous, prev_even = prev_even, current
        else:
            previous, prev_odd = prev_odd, current
        decoded[index] = ((previous ^ current) - 21 - bin(index).count("1")) & 0xFF

    # 首字节为 base36 的填充长度标记，其后为 32 字节 hex 密钥。
    # 标记校验失败不作为硬错误：上游两种实现对该字节的解释存在差异，
    # 密钥本体有效性由后续 hex 解码兜底。
    marker_ok = False
    try:
        padding_marker = int(chr(decoded[0]), 36)
        marker_ok = content_length - padding_marker - 1 == 32
    except ValueError:
        pass
    if not marker_ok:
        from nonebot import logger

        logger.debug(f"spade_a 填充标记校验未通过 (decoded[0]={decoded[0]})，按短剧下载器实现继续")

    try:
        key = binascii.unhexlify(decoded[1:33])
    except binascii.Error as e:
        raise ValueError("spade_a 载荷不是有效 hex 密钥") from e
    if len(key) != 16:
        raise ValueError(f"spade_a 密钥不是 AES-128: {len(key)} 字节")
    return key
