"""Regression tests for task ownership and the in-memory audio contract."""

from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest
import soundfile as sf

from src.audio.contracts import as_audio, to_mono
from src.audio.loader import AudioLoaderError, _check_audio_memory_budget, load_audio_multi_channel, save_audio, AudioData
from src.kernel.core.task_service import TaskBusyError, TaskService
from src.kernel.core.kernel_orchestrator import call_plugin_execute_async
from src.kernel.kernel import Kernel, _await_blocking


@pytest.mark.asyncio
async def test_task_admission_duplicate_conflict_and_cancel_once() -> None:
    service = TaskService()
    first, reused = service.admit("workshop", "analysis", ("bass", "plugin"))
    assert not reused
    duplicate, reused = service.admit("workshop", "analysis", ("bass", "plugin"))
    assert reused and duplicate is first
    with pytest.raises(TaskBusyError):
        service.admit("workshop", "separation", ("other",))

    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(60)

    handle = asyncio.create_task(work())
    service.attach(first, handle)
    await started.wait()
    service.cancel(first.id)
    service.cancel(first.id)  # repeated UI/close requests must not re-cancel a drain
    with pytest.raises(asyncio.CancelledError):
        await handle
    service.finish(first, "cancelled")
    assert service.active("workshop") is None
    assert service.get(first.id).status == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_blocking_worker_is_drained_before_release() -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking() -> str:
        started.set()
        assert release.wait(3)
        return "finished"

    handle = asyncio.create_task(_await_blocking(blocking))
    await asyncio.to_thread(started.wait, 2)
    handle.cancel()
    await asyncio.sleep(0.02)
    assert not handle.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handle, 2)


@pytest.mark.asyncio
async def test_sync_plugin_cancel_waits_for_model_thread() -> None:
    started = threading.Event()
    release = threading.Event()

    class Plugin:
        def execute(self, _rc, **_kwargs):
            started.set()
            assert release.wait(3)
            return {"status": "success"}

    handle = asyncio.create_task(call_plugin_execute_async(Plugin(), object()))
    await asyncio.to_thread(started.wait, 2)
    handle.cancel()
    await asyncio.sleep(0.02)
    assert not handle.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handle, 2)


@pytest.mark.asyncio
async def test_close_rejects_new_work_until_worker_drains() -> None:
    service = TaskService()
    record, _ = service.admit("w", "analysis", ("one",))
    worker_release = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await worker_release.wait()
            service.finish(record, "cancelled")

    handle = asyncio.create_task(work())
    service.attach(record, handle)
    drain = asyncio.create_task(service.drain_workshop("w"))
    await asyncio.sleep(0)
    with pytest.raises(TaskBusyError):
        service.admit("w", "upload", ("new",))
    assert not drain.done()
    worker_release.set()
    await asyncio.wait_for(drain, 2)
    service.end_close("w")


def test_audio_shape_mono_and_float_wav_roundtrip(tmp_path) -> None:
    source = np.linspace(-0.9, 0.9, 128, dtype=np.float64)
    stereo = as_audio(np.stack((source, -source), axis=1), 44100, layout="samples_first")
    assert stereo.shape == (2, 128)
    assert stereo.dtype == np.float32
    assert np.max(np.abs(to_mono(stereo))) < 1e-6
    path = tmp_path / "stem.wav"
    save_audio(path, AudioData(stereo, 44100, 128 / 44100))
    assert sf.info(path).subtype == "FLOAT"
    restored = load_audio_multi_channel(path)
    assert restored.channels == 2
    np.testing.assert_allclose(restored.samples, stereo, atol=1e-7)


def test_decode_budget_rejects_before_load(tmp_path) -> None:
    path = tmp_path / "large.wav"
    sf.write(path, np.zeros((44100, 2), dtype=np.float32), 44100)
    with pytest.raises(AudioLoaderError, match="超过预算"):
        _check_audio_memory_budget(path, budget_mb=1)
    with pytest.raises(ValueError):
        as_audio(np.array([[float("nan")]]), 44100)


@pytest.mark.asyncio
async def test_kernel_duplicate_separation_commits_one_terminal_event(tmp_path) -> None:
    kernel = Kernel(cache_root=tmp_path, autosave=False)
    kernel.boot()
    wid = kernel.create_workshop()["id"]
    events = kernel.bus.subscribe()
    first = kernel.start_separation_task(
        wid, plugin_name="example_separator", compute_device="cpu",
        audio_samples=np.zeros((2, 256), dtype=np.float32), sample_rate=44100,
        durations_sec=0,
    )
    duplicate = kernel.start_separation_task(
        wid, plugin_name="example_separator", compute_device="cpu",
        audio_samples=np.zeros((2, 256), dtype=np.float32), sample_rate=44100,
        durations_sec=0,
    )
    assert duplicate is first
    with pytest.raises(TaskBusyError):
        kernel.start_analysis_task(wid, plugin_name="example_analyzer", stem_name="bass")
    result = await first
    assert result["status"] == "success"
    ws = kernel.manager.get(wid)
    assert ws.get_separation_state() == "done"
    assert len(ws.get_track_audio_paths()) == 6
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    terminals = [event for event in emitted if event.type in {
        "separation_done", "separation_failed", "separation_cancelled",
    }]
    assert len(terminals) == 1
    assert terminals[0].payload["task_id"]
    assert kernel.tasks.active(wid) is None
    await kernel.shutdown_async()


@pytest.mark.asyncio
async def test_cancel_before_supervisor_starts_reconciles_workshop(tmp_path) -> None:
    kernel = Kernel(cache_root=tmp_path, autosave=False)
    kernel.boot()
    wid = kernel.create_workshop()["id"]
    handle = kernel.start_separation_task(
        wid, plugin_name="example_separator", compute_device="cpu",
        audio_samples=np.zeros((1, 64), dtype=np.float32), durations_sec=1,
    )
    record = kernel.tasks.active(wid)
    kernel.tasks.cancel(record.id)
    with pytest.raises(asyncio.CancelledError):
        await handle
    await asyncio.sleep(0)  # run the done callback
    assert kernel.tasks.active(wid) is None
    assert kernel.manager.get(wid).get_separation_state() == "cancelled"
    await kernel.shutdown_async()


def test_explicit_analysis_task_id_cannot_reuse_unowned_running_task(tmp_path) -> None:
    kernel = Kernel(cache_root=tmp_path, autosave=False)
    kernel.boot()
    wid = kernel.create_workshop()["id"]
    ws = kernel.manager.get(wid)
    old_id = ws.upsert_analysis_task("bass", "example_analyzer", task_id="old")
    new_id = ws.upsert_analysis_task("bass", "example_analyzer", task_id="new")
    assert old_id == "old"
    assert new_id == "new"
    assert "bass::new" in ws.state.tab_state.tab3
    kernel.shutdown()


@pytest.mark.asyncio
async def test_close_operation_drains_just_scheduled_task(tmp_path) -> None:
    kernel = Kernel(cache_root=tmp_path, autosave=False)
    kernel.boot()
    wid = kernel.create_workshop()["id"]
    kernel.start_separation_task(
        wid, plugin_name="example_separator", compute_device="cpu",
        audio_samples=np.zeros((1, 64), dtype=np.float32), durations_sec=1,
    )
    operation = kernel.schedule_workshop_close(wid)
    await asyncio.wait_for(operation.handle, 2)
    assert operation.status == "done"
    assert kernel.tasks.active(wid) is None
    assert kernel.manager.get(wid).get_separation_state() == "cancelled"
    await kernel.shutdown_async()
