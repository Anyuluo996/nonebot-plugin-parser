"""通用数字格式化工具（移植自 parser-lite 的 utils/format.py）。"""


def format_num(n: float | str) -> str:
    """将数字格式化为 万/亿 的中文表示。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)
