"""Process-local ownership and admission control for workshop jobs."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


class TaskBusyError(RuntimeError):
    """A workshop already has a conflicting job or is closing."""


TERMINAL = frozenset({"done", "failed", "cancelled", "interrupted"})
MAX_HISTORY = 1024


@dataclass
class TaskRecord:
    id: str
    workshop_id: str
    kind: str
    signature: tuple[Any, ...]
    plugin: str | None = None
    track: str | None = None
    status: str = "queued"
    stage: str = "queued"
    error: str | None = None
    progress: float | None = None
    progress_detail: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    handle: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.id,
            "workshop_id": self.workshop_id,
            "kind": self.kind,
            "plugin": self.plugin,
            "track": self.track,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "progress": self.progress,
            "progress_detail": dict(self.progress_detail),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TaskService:
    """One active mutating task per workshop, including its commit phase."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, TaskRecord] = {}
        self._active: dict[str, str] = {}
        self._closing: set[str] = set()
        self._operations: dict[str, str] = {}
        self._stopping = False

    def admit(
        self, wid: str, kind: str, signature: tuple[Any, ...], *,
        plugin: str | None = None, track: str | None = None,
    ) -> tuple[TaskRecord, bool]:
        with self._lock:
            if self._stopping or wid in self._closing:
                raise TaskBusyError(f"Workshop {wid} is closing")
            active_id = self._active.get(wid)
            if active_id is not None:
                active = self._records[active_id]
                if active.kind == kind and active.signature == signature:
                    return active, True
                raise TaskBusyError(f"Workshop {wid} already has task {active_id}")
            self._prune_history()
            record = TaskRecord(
                id=uuid4().hex, workshop_id=wid, kind=kind,
                signature=signature, plugin=plugin, track=track,
            )
            self._records[record.id] = record
            self._active[wid] = record.id
            return record, False

    def attach(self, record: TaskRecord, handle: asyncio.Task) -> None:
        with self._lock:
            record.handle = handle
            record.status = record.stage = "running"
            record.updated_at = time.time()

    def stage(self, record: TaskRecord, stage: str) -> None:
        with self._lock:
            if record.status in TERMINAL:
                return
            record.stage = stage
            record.progress = None
            record.progress_detail = {"device": record.progress_detail.get("device")}
            if stage == "committing":
                record.status = "committing"
            record.updated_at = time.time()

    def report_progress(self, record: TaskRecord, progress: float | None, **details) -> bool:
        with self._lock:
            if record.status in TERMINAL or record.status == "cancelling":
                return False
            record.stage = details.get("stage", record.stage)
            record.progress = progress
            record.progress_detail = {key: value for key, value in details.items() if key != "stage"}
            record.updated_at = time.time()
            return True

    def finish(self, record: TaskRecord, status: str, error: str | None = None) -> None:
        with self._lock:
            if record.status in TERMINAL:
                return
            record.status = record.stage = status
            record.error = error
            record.progress = 1.0 if status == "done" else None
            record.updated_at = time.time()
            if self._active.get(record.workshop_id) == record.id:
                self._active.pop(record.workshop_id, None)

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._records.get(task_id)

    def list_workshop(self, wid: str) -> list[dict[str, Any]]:
        with self._lock:
            return [r.snapshot() for r in self._records.values() if r.workshop_id == wid]

    def active(self, wid: str) -> TaskRecord | None:
        with self._lock:
            task_id = self._active.get(wid)
            return self._records.get(task_id) if task_id else None

    def operation(self, wid: str) -> TaskRecord | None:
        with self._lock:
            task_id = self._operations.get(wid)
            return self._records.get(task_id) if task_id else None

    def cancel(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status in TERMINAL:
                return record
            if record.status == "cancelling":
                return record
            if record.kind in {"close", "delete"}:
                raise TaskBusyError("A close/delete operation cannot be cancelled")
            if record.status == "committing":
                raise TaskBusyError("Task is committing and can no longer be cancelled")
            record.status = record.stage = "cancelling"
            record.updated_at = time.time()
            if record.handle is not None:
                record.handle.cancel()
            return record

    def begin_close(self, wid: str) -> TaskRecord | None:
        with self._lock:
            self._closing.add(wid)
            return self.active(wid)

    def end_close(self, wid: str) -> None:
        with self._lock:
            self._closing.discard(wid)
            self._operations.pop(wid, None)

    def create_operation(self, wid: str, kind: str) -> tuple[TaskRecord, bool]:
        with self._lock:
            existing = self._operations.get(wid)
            if existing is not None:
                return self._records[existing], True
            self._prune_history()
            record = TaskRecord(
                id=uuid4().hex, workshop_id=wid, kind=kind,
                signature=(kind,),
            )
            self._records[record.id] = record
            self._operations[wid] = record.id
            self._closing.add(wid)
            return record, False

    def _prune_history(self) -> None:
        """Bound process-local history without evicting running operations."""
        excess = len(self._records) - MAX_HISTORY + 1
        if excess <= 0:
            return
        for record in list(self._records.values()):
            if excess <= 0:
                break
            if record.status in TERMINAL and record.id not in self._operations.values():
                self._records.pop(record.id, None)
                excess -= 1

    async def drain_workshop(self, wid: str, *, cancel: bool = True) -> None:
        record = self.begin_close(wid)
        if record is None:
            return
        if cancel and record.status != "committing":
            self.cancel(record.id)
        if record.handle is not None:
            try:
                await asyncio.shield(record.handle)
            except (asyncio.CancelledError, Exception):
                pass

    async def drain_all(self) -> None:
        with self._lock:
            self._stopping = True
            ids = list(self._active)
        for wid in ids:
            await self.drain_workshop(wid)
        with self._lock:
            operations = [self._records[i].handle for i in self._operations.values()]
        for handle in operations:
            if handle is not None:
                try:
                    await asyncio.shield(handle)
                except (asyncio.CancelledError, Exception):
                    pass


__all__ = ["TaskService", "TaskRecord", "TaskBusyError", "TERMINAL"]
