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
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any, Literal

# 让 cache_system 在运行时可用
from src.kernel.core.cache_system import (  # noqa: F401
    CACHE_ROOT_DEFAULT,
)
from src.kernel.core.kernel_orchestrator import (  # noqa: F401
    Orchestrator,
    make_progress_callback,
)
from src.kernel.core.workshop import (  # noqa: F401
    WorkshopManager,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 事件总线（依据会议 §5.2）
# ---------------------------------------------------------------------------

EventType = Literal[
    # 车间生命周期
    "workshop_created",
    "workshop_deleted",
    "workshop_switched",
    "workshop_load_failed",
    # 音频输入
    "raw_audio_set",
    # 分离
    "separation_started",
    "separation_progress",
    "separation_done",
    "separation_failed",
    # 分析
    "analysis_started",
    "analysis_done",
    "analysis_failed",
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
    type: str            # EventType 之一（为兼容动态事件暂用 str）
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


# ---------------------------------------------------------------------------
# Kernel 顶层单例
# ---------------------------------------------------------------------------


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
        logger.info(
            "Kernel.boot: 加载 %d 车间，失败 %d", loaded, len(failed)
        )
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
        self._shutdown.set()
        if self.manager is not None:
            self.manager.shutdown()
        logger.info("Kernel.shutdown: 完成")

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
        mgr = self._require_manager()
        return mgr.close(wid)

    def delete_workshop(self, wid: str, *, keep_state: bool = False) -> bool:
        """删除车间（内存 + 磁盘）。

        Args:
            keep_state: ``True`` 时把 ``state.json`` 备份为 ``.bak`` 再删目录，
                方便用户反悔。
        """
        mgr = self._require_manager()
        return mgr.delete(wid, keep_state=keep_state)

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
        plugin_name: str = "example_separator",
        audio_samples=None,
        sample_rate: int = 22050,
        durations_sec: float = 3.0,
    ):
        """异步启动分离任务。

        Returns:
            :py:class:`asyncio.Task`，业务方 await 拿到 plugin 返回的 dict。
        """
        orch = self._require_orchestrator()
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        import numpy as np

        if audio_samples is not None:
            arr = np.asarray(audio_samples, dtype=np.float32)
            orch.rc.set_buffer("raw", arr)
            orch.rc.set_metadata("sample_rate", int(sample_rate))
        else:
            self._load_workshop_raw_audio_into_rc(wid, sample_rate=sample_rate)

        ws.start_separation(
            plugin_name,
            model_path=self._get_separator_model_path(plugin_name),
        )
        inner_task = orch.start_separation(
            wid,
            self.bus,
            plugin_name=plugin_name,
            durations_sec=durations_sec,
        )
        return asyncio.create_task(
            self._finalize_separation_task(wid, plugin_name, inner_task)
        )

    def _load_workshop_raw_audio_into_rc(
        self,
        wid: str,
        *,
        sample_rate: int = 22050,
    ) -> None:
        """Load the workshop's persisted raw audio into the orchestration RC."""
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        raw_path = ws.get_raw_audio_path()
        if raw_path is None:
            raw_path = self._recover_workshop_raw_audio_path(ws)
        if raw_path is None:
            raise RuntimeError(f"Workshop {wid} has no raw audio")

        from src.audio.loader import load_audio_multi_channel

        audio = load_audio_multi_channel(raw_path)
        orch = self._require_orchestrator()
        orch.rc.set_buffer("raw", audio.samples)
        orch.rc.set_metadata("sample_rate", int(audio.sample_rate))
        orch.rc.set_metadata("raw_audio_path", str(raw_path))

    def _load_workshop_stem_into_rc(
        self,
        wid: str,
        stem_name: str,
    ) -> None:
        """Ensure a separated stem track is in the RC buffer.

        If the stem is already in RC (same-session after separation), return
        immediately. Otherwise load it from the workshop cache on disk.
        """
        orch = self._require_orchestrator()
        try:
            orch.rc.get_buffer(stem_name)
            return  # already loaded
        except Exception:  # noqa: BLE001
            pass

        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        track_paths = ws.get_track_audio_paths()
        stem_path = track_paths.get(stem_name)
        if stem_path is None or not stem_path.is_file():
            raise RuntimeError(
                f"Stem '{stem_name}' not found in workshop {wid}. "
                f"Available: {sorted(track_paths.keys())}"
            )

        from src.audio.loader import load_audio

        audio = load_audio(stem_path)
        orch.rc.set_buffer(stem_name, audio.samples)
        if orch.rc.get_metadata("sample_rate") is None:
            orch.rc.set_metadata("sample_rate", int(audio.sample_rate))

    def _get_separator_model_path(self, plugin_name: str) -> str | None:
        """Return the manifest directory for a separator plugin when available."""
        orch = self._require_orchestrator()
        resolved_name = orch._resolve_separator_name(plugin_name)
        manifest = orch.pm.get_manifest(resolved_name)
        if manifest is None:
            return None
        manifest_dir = manifest.get("_manifest_dir")
        return str(manifest_dir) if manifest_dir else None

    @staticmethod
    def _recover_workshop_raw_audio_path(ws) -> Path | None:
        """Recover raw audio path from persisted workshop state when memory is stale."""
        try:
            raw_state = ws.cache.load_state() or {}
            rel_path = (
                raw_state.get("TabState", {})
                .get("Tab1", {})
                .get("RawAudioFilePath")
            )
            if not isinstance(rel_path, str) or not rel_path:
                return None
            raw_path = ws.cache.to_absolute(rel_path)
        except Exception:  # noqa: BLE001
            return None

        if not raw_path.is_file():
            return None
        ws.state.tab_state.tab1.raw_audio_file_path = rel_path
        return raw_path

    async def _finalize_separation_task(
        self,
        wid: str,
        plugin_name: str,
        inner_task,
    ) -> dict[str, Any]:
        """Persist separated stem buffers back into the workshop state."""
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        try:
            result = await inner_task
            if not isinstance(result, dict) or result.get("status") == "failed":
                error = (
                    result.get("error", "separation failed")
                    if isinstance(result, dict)
                    else "separation failed"
                )
                ws.fail_separation(str(error))
                return result

            track_files = self._persist_separated_tracks(wid, plugin_name)
            ws.complete_separation(track_files)
            return result
        except Exception as e:  # noqa: BLE001
            ws.fail_separation(str(e))
            raise

    def _persist_separated_tracks(
        self,
        wid: str,
        plugin_name: str,
    ) -> dict[str, str]:
        """Write RC stem buffers to workshop cache and return relative paths."""
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        orch = self._require_orchestrator()
        stems = orch.rc.get_metadata("separated_stems")
        if not stems:
            stems = ["vocals", "drums", "bass", "piano", "guitar", "other"]

        sample_rate = int(orch.rc.get_metadata("sample_rate") or 44100)
        track_files: dict[str, str] = {}

        from src.audio.loader import AudioData, save_audio

        for stem in stems:
            stem_name = str(stem)
            try:
                samples = orch.rc.get_buffer(stem_name)
            except Exception:  # noqa: BLE001
                continue
            audio_samples = self._normalize_audio_samples_for_save(samples)

            out_path = ws.cache.track_audio_path(
                stem_name,
                f"{plugin_name}_{stem_name}.wav",
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            save_audio(
                out_path,
                AudioData(
                    samples=audio_samples,
                    sample_rate=sample_rate,
                    duration=audio_samples.shape[-1] / sample_rate,
                ),
            )
            track_files[stem_name] = ws.cache.to_relative(out_path)

        if not track_files:
            raise RuntimeError("No separated stem buffers were produced")
        return track_files

    @staticmethod
    def _normalize_audio_samples_for_save(samples):
        """Return samples in AudioData's ``(channels, samples)`` convention."""
        if getattr(samples, "ndim", 0) == 2 and samples.shape[1] <= 8 < samples.shape[0]:
            return samples.T
        return samples

    def start_analysis_task(
        self,
        wid: str,
        *,
        plugin_name: str = "example_analyzer",
        stem_name: str = "vocals",
        durations_sec: float = 1.5,
    ):
        """异步启动分析任务。

        1. 调 ``ws.upsert_analysis_task()`` 标 running + emit
        2. Orchestrator 跑插件（SSE 推进度）
        3. 完成后持久化结果 → ws.complete_analysis()
        """
        orch = self._require_orchestrator()
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        task_id = ws.upsert_analysis_task(stem_name, plugin_name)

        # Load stem audio into RC so the plugin can read rc.get_buffer(stem_name)
        try:
            self._load_workshop_stem_into_rc(wid, stem_name)
        except RuntimeError as e:
            ws.fail_analysis(stem_name, task_id, str(e))
            raise

        inner_task = orch.start_analysis(
            wid,
            self.bus,
            plugin_name=plugin_name,
            stem_name=stem_name,
            durations_sec=durations_sec,
            emit_done_event=False,
        )
        return asyncio.create_task(
            self._finalize_analysis_task(wid, stem_name, task_id, plugin_name, inner_task)
        )

    async def _finalize_analysis_task(
        self,
        wid: str,
        stem_name: str,
        task_id: str,
        plugin_name: str,
        inner_task,
    ) -> dict[str, Any]:
        """Persist analysis results back into workshop cache + state."""
        mgr = self._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        try:
            result = await inner_task
            if not isinstance(result, dict) or result.get("status") == "failed":
                error = (
                    result.get("error", "analysis failed")
                    if isinstance(result, dict)
                    else "analysis failed"
                )
                ws.fail_analysis(stem_name, task_id, str(error))
                return result

            # Save result to cache/<wid>/analysis_result/<plugin>_result/result_<task_id>.json
            result_data = result.get("data", {}) if isinstance(result, dict) else {}
            if isinstance(result_data, list):
                result_data = {"chords": result_data}
            abs_path = ws.cache.save_analysis_result(
                plugin_name, task_id, result_data, ext="json"
            )
            rel = ws.cache.to_relative(abs_path)
            ws.complete_analysis(
                stem_name,
                task_id,
                rel,
                result=result_data,
            )
            return result
        except Exception as e:  # noqa: BLE001
            ws.fail_analysis(stem_name, task_id, str(e))
            raise


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


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
