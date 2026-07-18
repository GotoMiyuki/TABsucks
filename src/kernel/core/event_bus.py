"""进程级事件总线，用于 UI 实时推送。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "separation_started",
    "separation_progress",
    "separation_done",
    "separation_failed",
    "analysis_started",
    "analysis_done",
    "analysis_failed",
    "stale",
    "playback_state",
    "mix_state",
]


@dataclass(frozen=True)
class WorkshopEvent:
    workshop_id: str
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: float = 0.0

    def __post_init__(self) -> None:
        if self.emitted_at == 0.0:
            object.__setattr__(self, "emitted_at", time.time())


class EventBus:
    """进程级事件总线。MVP 阶段单进程足够。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[WorkshopEvent]]] = {}

    def subscribe(self, workshop_id: str) -> asyncio.Queue[WorkshopEvent]:
        q: asyncio.Queue[WorkshopEvent] = asyncio.Queue()
        self._subscribers.setdefault(workshop_id, []).append(q)
        return q

    def unsubscribe(self, workshop_id: str, q: asyncio.Queue[WorkshopEvent]) -> None:
        if q in self._subscribers.get(workshop_id, []):
            self._subscribers[workshop_id].remove(q)

    def emit(self, event: WorkshopEvent) -> None:
        for q in self._subscribers.get(event.workshop_id, []):
            q.put_nowait(event)


# 全局单例
bus = EventBus()
