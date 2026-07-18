"""Kernel ↔ PM ↔ AE 编排的端到端测试。

启动 :py:class:`Orchestrator` + :py:class:`EventBus`，模拟 plugin 通过
``progress_callback`` emit 事件，再验证 bus 收到的内容。
"""

from __future__ import annotations

import asyncio
import time

import numpy as np

from src.kernel.core.kernel_orchestrator import (
    Orchestrator,
    call_plugin_execute_async,
    make_progress_callback,
)
from src.kernel.kernel import EventBus


class TestOrchestratorInit:
    def test_orchestrator_registers_default_plugins(self) -> None:
        orch = Orchestrator(register_examples=True)
        names = orch.pm.list_plugins()
        assert "example_separator" in names
        assert "example_analyzer" in names

    def test_orchestrator_lists_plugin_metadata(self) -> None:
        orch = Orchestrator()
        seps = orch.list_separator_plugins()
        ans = orch.list_analyzer_plugins()
        assert any(s["name"] == "example_separator" for s in seps)
        assert any(a["name"] == "example_analyzer" for a in ans)


class TestProgressCallback:
    def test_callback_emits_to_bus(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        cb = make_progress_callback(bus, "wid1", "separation_progress")
        cb(0.5)
        ev = sub.get(timeout=0.5)
        assert ev.workshop_id == "wid1"
        assert ev.type == "separation_progress"
        assert ev.payload["progress"] == 0.5

    def test_callback_includes_extra(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        cb = make_progress_callback(
            bus,
            "wid1",
            "analysis_progress",
            extra={"track": "vocals"},
        )
        cb(0.1)
        ev = sub.get(timeout=0.5)
        assert ev.payload["track"] == "vocals"
        assert ev.payload["progress"] == 0.1


class TestStartSeparation:
    def test_gpu_request_falls_back_to_cpu_for_cpu_only_plugin(self) -> None:
        class CpuOnlyPlugin:
            name = "cpu_only_separator"
            captured_device: str | None = None

            def execute(self, rc, **kwargs):
                self.captured_device = kwargs["compute_device"]
                return {"status": "success", "data": {"stems": []}}

        bus = EventBus()
        orch = Orchestrator()
        plugin = CpuOnlyPlugin()
        orch.pm.register(plugin)
        sub_q = bus.subscribe()

        async def runner():
            await orch.start_separation(
                "wid_cpu_only",
                bus,
                plugin_name=plugin.name,
                audio_samples=np.zeros(128, dtype=np.float32),
                compute_device="gpu",
                durations_sec=0.0,
            )

        asyncio.run(runner())

        assert plugin.captured_device == "cpu"
        started = next(
            event
            for event in _drain_queue_until_terminal(sub_q)
            if event.type == "separation_started"
        )
        assert started.payload["requested_device"] == "gpu"
        assert started.payload["effective_device"] == "cpu"

    def test_gpu_capable_plugin_receives_gpu_request(
        self,
        monkeypatch,
    ) -> None:
        class GpuPlugin:
            name = "gpu_separator"
            captured_device: str | None = None

            def execute(self, rc, **kwargs):
                self.captured_device = kwargs["compute_device"]
                return {"status": "success", "data": {"stems": []}}

        bus = EventBus()
        orch = Orchestrator()
        plugin = GpuPlugin()
        orch.pm.register(plugin)
        orch.pm._manifests[plugin.name] = {
            "name": plugin.name,
            "supported_devices": ["cpu", "gpu"],
            "requirements": {"gpu_memory_mb": 1},
        }
        monkeypatch.setattr(
            orch.rc,
            "get_gpu_info",
            lambda: {"cuda_available": True},
        )
        monkeypatch.setattr(
            orch.pm,
            "prepare_vram",
            lambda name: {"ready": True},
        )

        async def runner():
            await orch.start_separation(
                "wid_gpu",
                bus,
                plugin_name=plugin.name,
                audio_samples=np.zeros(128, dtype=np.float32),
                compute_device="gpu",
                durations_sec=0.0,
            )

        asyncio.run(runner())

        assert plugin.captured_device == "gpu"

    def test_emits_started_progress_done(self) -> None:
        """完整链：started → progress×N → done。"""
        bus = EventBus()
        orch = Orchestrator()
        sub_q = bus.subscribe()
        samples = np.zeros(22050 * 2, dtype=np.float32)

        async def runner():
            await orch.start_separation(
                "wid_test",
                bus,
                plugin_name="example_separator",
                audio_samples=samples,
                sample_rate=22050,
                durations_sec=0.0,
            )

        asyncio.run(runner())

        evs = _drain_queue_until_terminal(sub_q)
        types = [e.type for e in evs]
        assert types[0] == "separation_started"
        assert "separation_done" in types
        assert "separation_failed" not in types

        progress_evs = [e for e in evs if e.type == "separation_progress"]
        assert len(progress_evs) >= 1

    def test_emits_done_with_stems(self) -> None:
        bus = EventBus()
        orch = Orchestrator()
        sub_q = bus.subscribe()

        async def runner():
            await orch.start_separation(
                "wid_stems",
                bus,
                audio_samples=np.zeros(22050, dtype=np.float32),
                durations_sec=0.0,
            )

        asyncio.run(runner())
        evs = _drain_queue_until_terminal(sub_q)
        done = next(e for e in evs if e.type == "separation_done")
        assert "vocals" in done.payload["stems"]
        assert "other" in done.payload["stems"]

    def test_unknown_plugin_emits_failed(self) -> None:
        bus = EventBus()
        orch = Orchestrator()
        sub_q = bus.subscribe()

        async def runner():
            await orch.start_separation(
                "wid_fail",
                bus,
                plugin_name="non_existent_plugin",
                audio_samples=np.zeros(22050, dtype=np.float32),
                durations_sec=0.0,
            )

        asyncio.run(runner())
        evs = _drain_queue_until_terminal(sub_q)
        failed = next(e for e in evs if e.type == "separation_failed")
        assert "non_existent_plugin" in failed.payload.get("plugin", "")

    def test_sync_plugin_without_progress_gets_heartbeat(self) -> None:
        class SlowSyncPlugin:
            def execute(self, rc, **kwargs):
                time.sleep(0.16)
                return {"status": "success", "data": {"stems": []}}

        progress: list[float] = []

        async def runner():
            return await call_plugin_execute_async(
                SlowSyncPlugin(),
                rc=None,
                progress_interval_sec=0.05,
                durations_sec=0.0,
                progress_callback=progress.append,
            )

        result = asyncio.run(runner())

        assert result["status"] == "success"
        assert any(0.0 < p < 1.0 for p in progress)

    def test_sync_plugin_with_native_progress_does_not_get_heartbeat(self) -> None:
        class NativeProgressPlugin:
            reports_progress = True

            def execute(self, rc, **kwargs):
                kwargs["progress_callback"](0.25)
                time.sleep(0.16)
                return {"status": "success", "data": {}}

        progress: list[float] = []

        async def runner():
            return await call_plugin_execute_async(
                NativeProgressPlugin(),
                rc=None,
                progress_interval_sec=0.05,
                durations_sec=0.0,
                progress_callback=progress.append,
            )

        result = asyncio.run(runner())

        assert result["status"] == "success"
        assert progress == [0.25]


class TestStartAnalysis:
    def test_emits_done_with_chords(self) -> None:
        bus = EventBus()
        orch = Orchestrator()
        # 准备 stem buffer
        orch.rc.set_buffer("vocals", np.zeros(22050, dtype=np.float32))
        orch.rc.set_metadata("sample_rate", 22050)
        sub_q = bus.subscribe()

        async def runner():
            await orch.start_analysis(
                "wid_analyze",
                bus,
                plugin_name="example_analyzer",
                stem_name="vocals",
                durations_sec=0.0,
            )

        asyncio.run(runner())
        evs = _drain_queue_until_terminal(sub_q)
        done = next(e for e in evs if e.type == "analysis_done")
        assert done.payload["track"] == "vocals"
        chords = done.payload["result"].get("chords", [])
        assert len(chords) == 4

    def test_can_defer_done_event_to_kernel_finalize(self) -> None:
        bus = EventBus()
        orch = Orchestrator()
        orch.rc.set_buffer("vocals", np.zeros(22050, dtype=np.float32))
        orch.rc.set_metadata("sample_rate", 22050)
        sub_q = bus.subscribe()

        async def runner():
            await orch.start_analysis(
                "wid_analyze",
                bus,
                plugin_name="example_analyzer",
                stem_name="vocals",
                durations_sec=0.0,
                emit_done_event=False,
            )

        asyncio.run(runner())
        events = []
        while not sub_q.empty():
            events.append(sub_q.get_nowait())

        assert any(event.type == "analysis_started" for event in events)
        assert not any(event.type == "analysis_done" for event in events)


def _drain_queue_until_terminal(
    q,
    *,
    terminal_types: tuple[str, ...] = ("separation_done", "analysis_done", "separation_failed", "analysis_failed"),
    extra_timeout: float = 2.0,
    max_items: int = 500,
) -> list:
    """读 Queue，直到见到 ``terminal_types`` 之一或超时。"""
    items: list = []
    deadline_loops = int(extra_timeout / 0.05)
    seen_terminal = False

    for _ in range(max_items):
        try:
            ev = q.get(timeout=0.05)
        except Exception:  # noqa: BLE001
            if seen_terminal:
                break
            deadline_loops -= 1
            if deadline_loops <= 0:
                break
            continue
        items.append(ev)
        if ev.type in terminal_types:
            seen_terminal = True
            # 读完剩余 microtasks 内产生的事件，但不要无限等
            for _ in range(5):
                try:
                    items.append(q.get(timeout=0.05))
                except Exception:  # noqa: BLE001
                    break
            break
    return items
