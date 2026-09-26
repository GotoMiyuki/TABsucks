"""TABsucks 内核进程入口。

层级：

.. code-block:: text

    kernel.py                 ← 进程入口，装配 EventBus + WorkshopManager
      ├─ EventBus             ← 进程级发布订阅
      └─ WorkshopManager      ← 多车间管理
          └─ MusicWorkshop    ← 单车间运行时
              ├─ WorkshopState
              └─ WorkshopCache (from cache_system)

生命周期：

* :py:meth:`Kernel.boot` — 装载所有车间（坏车间跳过）
* :py:meth:`Kernel.run` — 主循环（占位 sleep，等待 Ctrl-C）
* :py:meth:`Kernel.shutdown` — 刷盘所有车间，停止 autosave 线程

事件：

* 进程级 EventBus（任意车间内 __emit__ → 全进程可见）
* 推荐订阅方式：通过 :py:meth:`Kernel.subscribe_events` 拿到队列
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any

# 让 cache_system 在运行时可用
from src.kernel.core.cache_system import (  # noqa: F401
    CACHE_ROOT_DEFAULT,
)
from src.kernel.core.kernel_orchestrator import (  # noqa: F401
    Orchestrator,
)
from src.kernel.core.task_service import TERMINAL, TaskBusyError, TaskRecord, TaskService
from src.kernel.core.workshop_manager import (  # noqa: F401
    WorkshopManager,
)

from .core.async_workers import await_blocking as _await_blocking  # noqa: F401
from .core.event_bus import EventBus, EventType, WorkshopEvent
from .core.workshop_jobs import WorkshopJobs

logger = logging.getLogger(__name__)


class Kernel:
    """TABsucks 进程级核心。

    用法：

    .. code-block:: python

        kernel = Kernel()
        kernel.boot()
        try:
            kernel.run()
        except KeyboardInterrupt:
            kernel.shutdown()
    """

    def __init__(
        self,
        cache_root: Path | None = None,
        event_bus: EventBus | None = None,
        autosave: bool = True,
    ) -> None:
        self.cache_root: Path = (
            Path(cache_root) if cache_root is not None else CACHE_ROOT_DEFAULT
        ).resolve()
        self.bus: EventBus = event_bus or EventBus()
        self._autosave = autosave
        self.manager: WorkshopManager | None = None
        self.orchestrator: Orchestrator | None = None
        self.tasks = TaskService()
        self.jobs = WorkshopJobs(self)
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def boot(self) -> tuple[int, list[tuple[str, str]]]:
        """装配 + 扫描加载所有车间。

        Returns:
            ``(loaded_count, failed)``
        """
        self.orchestrator = Orchestrator()
        self.manager = WorkshopManager(
            cache_root=self.cache_root,
            event_bus=self.bus,
            autosave=self._autosave,
        )
        loaded, failed = self.manager.load_all()
        logger.info("Kernel.boot: 加载 %d 车间，失败 %d", loaded, len(failed))
        return loaded, failed

    def run(self) -> None:
        """主循环：MVP 阶段是占位（sleep + 等信号）。

        未来可在此处挂 HTTP / WebSocket 服务。
        """
        logger.info("Kernel.run: 进入主循环（Ctrl-C 退出）")
        while not self._shutdown.is_set():
            time.sleep(0.1)

    def shutdown(self) -> None:
        """刷盘所有车间 + 清理 autosave 线程。"""
        if self._shutdown.is_set():
            return
        if (
            any(self.tasks.active(wid) for wid in self.manager.list_ids())
            if self.manager
            else False
        ):
            raise RuntimeError("Active tasks require await Kernel.shutdown_async()")
        self._shutdown.set()
        if self.manager is not None:
            self.manager.shutdown()
        if self.orchestrator is not None:
            self.orchestrator.shutdown()
        logger.info("Kernel.shutdown: 完成")

    async def shutdown_async(self) -> None:
        """Stop admission and wait for model workers before clearing resources."""
        if self._shutdown.is_set():
            return
        await self.tasks.drain_all()
        self.shutdown()

    # ------------------------------------------------------------------
    # 给上层（HTTP / UI）调用的快捷 API
    # ------------------------------------------------------------------

    def _require_manager(self) -> WorkshopManager:
        if self.manager is None:
            raise RuntimeError("Kernel 未启动，请先调用 boot()")
        return self.manager

    def list_workshops(self) -> list[dict[str, Any]]:
        """序列化所有车间（HTTP 用）。"""
        mgr = self._require_manager()
        result: list[dict[str, Any]] = []
        for ws in mgr.list_workshops():
            result.append(
                {
                    "id": ws.id,
                    "name": ws.name,
                    "last_tab": ws.last_tab,
                }
            )
        return result

    def create_workshop(self, name: str = "New Workshop") -> dict[str, Any]:
        mgr = self._require_manager()
        ws = mgr.create(name)
        return {"id": ws.id, "name": ws.name, "last_tab": ws.last_tab}

    def switch_workshop(self, wid: str) -> bool:
        mgr = self._require_manager()
        return mgr.switch_to(wid)

    def get_state(self, wid: str) -> dict[str, Any] | None:
        """HTTP GET /api/workshops/<wid>/state 用。"""
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            return None
        return ws.to_dict()

    def close_workshop(self, wid: str) -> bool:
        """关闭车间（仅释放内存，磁盘数据保留，下次启动自动加载）。"""
        if self.tasks.active(wid) is not None:
            raise TaskBusyError(f"Workshop {wid} has an active task")
        mgr = self._require_manager()
        closed = mgr.close(wid)
        if closed and self.orchestrator is not None:
            self.orchestrator.release_context(wid)
        return closed

    def delete_workshop(self, wid: str, *, keep_state: bool = False) -> bool:
        """删除车间（内存 + 磁盘）。

        Args:
            keep_state: ``True`` 时把 ``state.json`` 备份为 ``.bak`` 再删目录，
                方便用户反悔。
        """
        if self.tasks.active(wid) is not None:
            raise TaskBusyError(f"Workshop {wid} has an active task")
        mgr = self._require_manager()
        deleted = mgr.delete(wid, keep_state=keep_state)
        if deleted and self.orchestrator is not None:
            self.orchestrator.release_context(wid)
        return deleted

    def schedule_workshop_close(
        self,
        wid: str,
        *,
        delete: bool = False,
        keep_state: bool = False,
    ) -> TaskRecord:
        """Drain an active job, then close or delete the workshop."""
        if self._require_manager().get(wid) is None:
            raise KeyError(wid)
        operation, reused = self.tasks.create_operation(wid, "delete" if delete else "close")
        if reused:
            return operation

        async def _run() -> None:
            try:
                await self.tasks.drain_workshop(wid)
                if delete:
                    self.delete_workshop(wid, keep_state=keep_state)
                else:
                    self.close_workshop(wid)
                self.tasks.finish(operation, "done")
            except Exception as error:
                self.tasks.finish(operation, "failed", str(error))
                logger.exception("Workshop %s %s failed", wid, operation.kind)
            finally:
                self.tasks.end_close(wid)

        self.tasks.attach(operation, asyncio.create_task(_run()))
        return operation

    def _reconcile_task_exit(self, record: TaskRecord, handle: asyncio.Task) -> None:
        """Handle cancellation before a newly scheduled supervisor first runs."""
        if record.status in TERMINAL:
            return
        ws = self.manager.get(record.workshop_id) if self.manager else None
        cancelled = handle.cancelled()
        error = None if cancelled else handle.exception()
        failure = str(error or "Task exited without terminal status")
        try:
            if ws is not None:
                if record.kind == "separation":
                    if cancelled:
                        ws.cancel_separation(task_id=record.id)
                    else:
                        ws.fail_separation(failure, task_id=record.id)
                elif record.kind == "analysis" and record.track is not None:
                    if cancelled:
                        ws.cancel_analysis(record.track, record.id)
                    else:
                        ws.fail_analysis(record.track, record.id, failure)
        except Exception:
            logger.exception("Failed to reconcile task %s", record.id)
        finally:
            self.tasks.finish(
                record,
                "cancelled" if cancelled else "failed",
                None if cancelled else failure,
            )

    def rename_workshop(self, wid: str, new_name: str) -> bool:
        mgr = self._require_manager()
        return mgr.rename(wid, new_name)

    def suggest_workshop_name(self, source: str | Path | None = None) -> str:
        """根据来源（URL / 本地路径 / video title）建议车间名。

        是 :py:func:`src.utils.naming.suggest_workshop_name` 的 thin wrapper，
        方便 UI / HTTP 层只调 Kernel 而不必直接 import utils。
        """
        from src.utils.naming import suggest_workshop_name

        return suggest_workshop_name(source)

    def subscribe_events(self) -> Queue[WorkshopEvent]:
        """HTTP SSE 端点调这个，回 Queue 给客户端。"""
        return self.bus.subscribe()

    # ------------------------------------------------------------------
    # 编排层快捷方法（编排 PM/AE/RC）
    # ------------------------------------------------------------------

    def _require_orchestrator(self) -> Orchestrator:
        if self.orchestrator is None:
            raise RuntimeError("Kernel 未启动，请先调用 boot()")
        return self.orchestrator

    def list_separator_plugins(self) -> list[dict[str, Any]]:
        """列出可用的分离插件（给 UI 下拉列表）。

        编排层委托：MVP 阶段返回 ``Orchestrator.list_separator_plugins()``
        列表。未来走 :py:mod:`src.plugins.separation` 的 manifest 扫盘。
        """
        return self._require_orchestrator().list_separator_plugins()

    def list_analyzer_plugins(self) -> list[dict[str, Any]]:
        """列出可用的分析插件。"""
        return self._require_orchestrator().list_analyzer_plugins()

    def start_separation_task(
        self,
        wid: str,
        *,
        plugin_name: str = "separation_bs_roformer",
        audio_samples=None,
        sample_rate: int = 22050,
        compute_device: str = "gpu",
        durations_sec: float = 3.0,
    ):
        """Admit a separation job; the returned handle owns persistence too."""
        return self.jobs.start_separation_task(
            wid,
            plugin_name=plugin_name,
            audio_samples=audio_samples,
            sample_rate=sample_rate,
            compute_device=compute_device,
            durations_sec=durations_sec,
        )

    def start_analysis_task(
        self,
        wid: str,
        *,
        plugin_name: str = "example_analyzer",
        stem_name: str = "vocals",
        durations_sec: float = 1.5,
    ):
        """Admit a per-track analysis job through the execution service."""
        return self.jobs.start_analysis_task(
            wid,
            plugin_name=plugin_name,
            stem_name=stem_name,
            durations_sec=durations_sec,
        )

    # Compatibility for callers of the pre-split audio helpers.
    def _load_workshop_raw_audio_into_rc(self, *args, **kwargs):
        return self.jobs._load_workshop_raw_audio_into_rc(*args, **kwargs)

    _recover_workshop_raw_audio_path = staticmethod(WorkshopJobs._recover_workshop_raw_audio_path)


def main() -> None:
    """``python -m src.kernel.kernel`` 的入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    kernel = Kernel()
    kernel.boot()
    try:
        kernel.run()
    except KeyboardInterrupt:
        kernel.shutdown()


__all__ = [
    "EventType",
    "WorkshopEvent",
    "EventBus",
    "Kernel",
    "main",
]
