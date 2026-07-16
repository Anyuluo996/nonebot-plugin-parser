r"""验证 NGA JSON 非法 \u 转义清洗（CI 实测 NGA 偶发返回非法 \uXXXX 导致 JSONDecodeError）"""

import re
import json

import pytest


def _sanitize(blob: str) -> str:
    # \u 后非4位hex → 把这个 \u 降级为普通字符（转义反斜杠）
    return re.sub(r"\\u(?![0-9a-fA-F]{4})", r"\\\\u", blob)


def test_valid_unicode_escape_preserved():
    r"""合法的 \uXXXX 应原样保留"""
    raw = r'{"content":"\u4f60\u597d"}'  # 你好
    cleaned = _sanitize(raw)
    data = json.loads(cleaned)
    assert data["content"] == "你好"


def test_invalid_u_escape_fixed():
    r"""非法 \u 转义（如 \u 后跟非hex）应被清洗后能解析"""
    raw = r'{"content":"a \uXYZ b"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    cleaned = _sanitize(raw)
    data = json.loads(cleaned)
    assert "a" in data["content"]
    assert "b" in data["content"]


def test_mixed_escape():
    r"""混合合法与非法 \u 转义"""
    raw = r'{"a":"\u4f60 \uXYZ \u597d"}'
    cleaned = _sanitize(raw)
    data = json.loads(cleaned)
    assert "你" in data["a"]
    assert "好" in data["a"]
