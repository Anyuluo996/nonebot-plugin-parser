"""点歌选择状态存储（窗口期 + 用户隔离）。

用户 ``par点歌`` 后,候选列表存入内存（不持久化,重启即失效,符合点歌即时性）。
``par<序号>`` 选择时按 ``user_id@scene_id`` 隔离取回,过期自动清理。

设计要点:
- **用户隔离**: A 用户的候选不影响 B 用户,避免错选他人结果。
- **场景隔离**: 同一用户在私聊/不同群各自独立维护候选。
- **窗口期**: 默认 5 分钟,超期提示重新搜索,避免选到很久之前的陈旧结果。
- **覆盖语义**: 同一 user@scene 再次 ``par点歌`` 覆盖旧候选（自然行为,无需额外清理）。
"""

from __future__ import annotations

import time
from dataclasses import field, dataclass

from .music_search import SearchItem

# 窗口期（秒）: 点歌候选在此时长内可被选择,超期失效
ORDER_EXPIRES_SECONDS = 300


def _make_session_key(user_id: str, scene_id: str) -> str:
    """构造隔离 key: ``user_id@scene_id``。"""
    return f"{user_id}@{scene_id}"


@dataclass(slots=True)
class OrderSession:
    """一次点歌的候选会话。

    Attributes:
        items: 候选 ``SearchItem`` 列表（序号即列表索引 +1）。
        keyword: 原始搜索词（供提示文案使用）。
        created_at: 创建时间戳（秒）。
    """

    items: list[SearchItem] = field(default_factory=list)
    keyword: str = ""
    created_at: float = field(default_factory=time.time)

    def is_expired(self, now: float | None = None) -> bool:
        """是否已过窗口期。"""
        now = now if now is not None else time.time()
        return (now - self.created_at) > ORDER_EXPIRES_SECONDS


class _OrderStore:
    """内存存储: session_key → OrderSession。

    简单 dict 实现,点歌频率低无需 LRU/锁;覆盖语义天然支持「重新搜索」。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, OrderSession] = {}

    def save(self, user_id: str, scene_id: str, items: list[SearchItem], keyword: str) -> None:
        """保存候选（覆盖该用户在该场景下的旧会话）。"""
        self._sessions[_make_session_key(user_id, scene_id)] = OrderSession(
            items=items,
            keyword=keyword,
        )

    def get(self, user_id: str, scene_id: str) -> OrderSession | None:
        """取回有效会话,过期则清理并返回 None。"""
        key = _make_session_key(user_id, scene_id)
        session = self._sessions.get(key)
        if session is None:
            return None
        if session.is_expired():
            self._sessions.pop(key, None)
            return None
        return session

    def clear(self, user_id: str, scene_id: str) -> None:
        """主动清理某个会话。"""
        self._sessions.pop(_make_session_key(user_id, scene_id), None)

    def clear_all(self) -> None:
        """清空全部会话（测试用）。"""
        self._sessions.clear()


# 全局单例
ORDER_STORE = _OrderStore()
