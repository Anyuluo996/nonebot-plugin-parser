"""通用小工具：缓存字典、文本清洗、文件操作与格式化。

无内部依赖，供本包 media / imaging 及插件其他模块共用。
"""

import re
import hashlib
import importlib.util
from typing import Any, TypeVar
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urlparse

from anyio import Path as AnyioPath
from nonebot import logger

K = TypeVar("K")
V = TypeVar("V")


class LimitedSizeDict(OrderedDict[K, V]):
    def __init__(self, *args, max_size=20, **kwargs):
        self.max_size = max_size
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: K, value: V):
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)  # 移除最早添加的项


def keep_zh_en_num(text: str) -> str:
    """保留字符串中的中英文和数字"""
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\-_]", "", text.replace(" ", "_"))


async def safe_unlink(path: Path):
    """安全删除文件"""
    await AnyioPath(path).unlink(missing_ok=True)


def fmt_size(file_path: Path) -> str:
    """格式化文件大小"""
    return f"大小: {file_path.stat().st_size / 1024 / 1024:.2f} MB"


def fmt_duration(duration: float) -> str:
    """格式化媒体时长，超过 1 小时后显示为 h:mm:ss。"""
    total_seconds = max(int(duration), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def generate_file_name(url: str, default_suffix: str = "") -> str:
    """根据 url 生成文件名"""

    # 根据 url 获取文件后缀
    path = Path(urlparse(url).path)
    suffix = path.suffix if path.suffix else default_suffix
    # 获取 url 的 md5 值
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    file_name = f"{url_hash}{suffix}"
    return file_name


def write_json_to_data(data: dict[str, Any] | str, file_name: str):
    """将数据写入数据目录"""
    import json

    from ..config import pconfig

    path = pconfig.data_dir / file_name
    if isinstance(data, str):
        data = json.loads(data)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    logger.success(f"数据写入 {path} 成功")


def is_module_available(module_name: str) -> bool:
    """检查模块是否可用"""
    return importlib.util.find_spec(module_name) is not None
