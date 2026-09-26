"""The single, thread-safe process event bus used by Kernel and SSE."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from queue import Queue
from typing import Any, Literal

logger = logging.getLogger(__name__)

EventType = Literal[
    # 车间生命周期
    "workshop_created",
    "workshop_deleted",
    "workshop_closed",
    "workshop_switched",
    "workshop_load_failed",
    # 音频输入
    "raw_audio_set",
    # 分离
    "separation_started",
    "separation_progress",
    "separation_done",
    "separation_failed",
    "separation_cancelled",
    # 分析
    "analysis_started",
    "analysis_done",
    "analysis_failed",
    "analysis_progress",
    "analysis_cancelled",
    "url_download_progress",
    # 播放/混音
    "mix_state_changed",
    "playback_state",
    # 状态
    "state_saved",
]


@dataclass(frozen=True)
class WorkshopEvent:
    """事件载荷。"""

    workshop_id: str
    type: str  # EventType 之一（为兼容动态事件暂用 str）
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: float = field(default_factory=time.time)


class EventBus:
    """进程级事件总线（MVP 阶段单进程足够）。

    用法：

    .. code-block:: python

        bus = EventBus()
        # 业务侧 emit：
        bus.emit("abc", "separation_done", {"tracks": ["vocals", "drums"]})
        # 订阅侧：
        q = bus.subscribe()
        ev = q.get(timeout=1.0)
    """

    def __init__(self) -> None:
        # 每个订阅者一个无界 Queue；MVP 阶段先这样，未来用 asyncio.Queue 替换
        self._subscribers: list[Queue[WorkshopEvent]] = []
        self._lock = threading.RLock()

    # ---- 订阅 ----

    def subscribe(self) -> Queue[WorkshopEvent]:
        """注册一个订阅者，返回其事件队列。"""
        q: Queue[WorkshopEvent] = Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue[WorkshopEvent]) -> None:
        """取消订阅。"""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    # ---- 发送 ----

    def emit(
        self,
        workshop_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """向所有订阅者推事件。"""
        ev = WorkshopEvent(
            workshop_id=workshop_id,
            type=event_type,
            payload=dict(payload or {}),
        )
        with self._lock:
            subscribers = list(self._subscribers)  # 防止迭代时变动
        for q in subscribers:
            try:
                q.put_nowait(ev)
            except Exception:  # noqa: BLE001
                logger.debug("EventBus 推送失败，丢弃 event=%s", event_type)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量（仅用于调试）。"""
        with self._lock:
            return len(self._subscribers)
