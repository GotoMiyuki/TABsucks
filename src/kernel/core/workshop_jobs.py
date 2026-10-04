"""Owned separation/analysis execution and result persistence.

Kernel owns lifecycle and admission; this service owns job execution.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .async_workers import await_blocking as _await_blocking
from .task_service import TaskBusyError, TaskRecord

if TYPE_CHECKING:
    from ..kernel import Kernel

logger = logging.getLogger(__name__)


class WorkshopJobs:
    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel

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
        """异步启动分离任务。

        Returns:
            :py:class:`asyncio.Task`，业务方 await 拿到 plugin 返回的 dict。
        """
        self.kernel._require_orchestrator()
        mgr = self.kernel._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")
        record, reused = self.kernel.tasks.admit(
            wid,
            "separation",
            (plugin_name, compute_device, ws.state.tab_state.tab1.raw_audio_file_path),
            plugin=plugin_name,
        )
        if reused:
            if record.handle is None:
                raise TaskBusyError("Task is being prepared")
            return record.handle
        try:
            ws.start_separation(
                plugin_name,
                model_path=self._get_separator_model_path(plugin_name, wid=wid),
                task_id=record.id,
            )
            handle = asyncio.create_task(
                self._supervise_separation(
                    record,
                    plugin_name,
                    audio_samples,
                    sample_rate,
                    compute_device,
                    durations_sec,
                )
            )
            self.kernel.tasks.attach(record, handle)
            handle.add_done_callback(
                lambda finished: self.kernel._reconcile_task_exit(record, finished)
            )
            return handle
        except BaseException as error:
            self.kernel.tasks.finish(record, "failed", str(error))
            raise

    async def _supervise_separation(
        self,
        record: TaskRecord,
        plugin_name: str,
        audio_samples,
        sample_rate: int,
        compute_device: str,
        durations_sec: float,
    ) -> dict[str, Any]:
        ws = self.kernel._require_manager().get(record.workshop_id)
        try:
            self.kernel.tasks.stage(record, "loading_audio")
            if audio_samples is not None:
                self.kernel._require_orchestrator().get_context(
                    record.workshop_id
                ).rc.set_audio_buffer("raw", audio_samples, int(sample_rate))
                self.kernel._require_orchestrator().get_context(record.workshop_id).rc.set_metadata(
                    "sample_rate", int(sample_rate)
                )
            else:
                await _await_blocking(
                    self._load_workshop_raw_audio_into_rc,
                    record.workshop_id,
                    sample_rate=sample_rate,
                )
            self.kernel.tasks.stage(record, "running_plugin")
            inner_task = self.kernel._require_orchestrator().start_separation(
                record.workshop_id,
                self.kernel.bus,
                plugin_name=plugin_name,
                compute_device=compute_device,
                durations_sec=durations_sec,
                emit_lifecycle=False,
                task_id=record.id,
            )
            result = await self._finalize_separation_task(
                record.workshop_id, plugin_name, inner_task, record
            )
            self.kernel.tasks.finish(
                record,
                "failed" if result.get("status") == "failed" else "done",
                result.get("error"),
            )
            return result
        except asyncio.CancelledError:
            try:
                if ws is not None:
                    ws.cancel_separation(task_id=record.id)
            finally:
                if self.kernel.orchestrator is not None:
                    self.kernel.orchestrator.get_context(record.workshop_id).rc.clear()
                self.kernel.tasks.finish(record, "cancelled")
            return {"status": "cancelled"}
        except Exception as error:
            try:
                if ws is not None and ws.get_separation_state() == "running":
                    ws.fail_separation(str(error), task_id=record.id)
            finally:
                self.kernel.tasks.finish(record, "failed", str(error))
            raise

    def _load_workshop_raw_audio_into_rc(
        self,
        wid: str,
        *,
        sample_rate: int = 22050,
    ) -> None:
        """Load the workshop's persisted raw audio into the orchestration RC."""
        mgr = self.kernel._require_manager()
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
        orch = self.kernel._require_orchestrator()
        context = orch.get_context(wid)
        context.rc.set_audio_buffer("raw", audio.samples, int(audio.sample_rate))
        context.rc.set_metadata("sample_rate", int(audio.sample_rate))
        context.rc.set_metadata("raw_audio_path", str(raw_path))

    def _load_workshop_stem_into_rc(
        self,
        wid: str,
        stem_name: str,
    ) -> None:
        """Ensure a separated stem track is in the RC buffer.

        If the stem is already in RC (same-session after separation), return
        immediately. Otherwise load it from the workshop cache on disk.
        """
        orch = self.kernel._require_orchestrator()
        context = orch.get_context(wid)
        try:
            context.rc.get_audio_buffer(stem_name)
            return  # already loaded
        except Exception:  # noqa: BLE001
            pass

        mgr = self.kernel._require_manager()
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

        from src.audio.loader import load_audio_multi_channel

        audio = load_audio_multi_channel(stem_path)
        context.rc.set_audio_buffer(stem_name, audio.samples, int(audio.sample_rate))
        if context.rc.get_metadata("sample_rate") is None:
            context.rc.set_metadata("sample_rate", int(audio.sample_rate))

    def _get_separator_model_path(
        self,
        plugin_name: str,
        *,
        wid: str | None = None,
    ) -> str | None:
        """Return the manifest directory for a separator plugin when available."""
        orch = self.kernel._require_orchestrator()
        pm = orch.get_context(wid).pm if wid is not None else orch.pm
        resolved_name = orch._resolve_separator_name(plugin_name, pm)
        manifest = pm.get_manifest(resolved_name)
        if manifest is None:
            return None
        manifest_dir = manifest.get("_manifest_dir")
        return str(manifest_dir) if manifest_dir else None

    @staticmethod
    def _recover_workshop_raw_audio_path(ws) -> Path | None:
        """Recover raw audio path from persisted workshop state when memory is stale."""
        try:
            raw_state = ws.cache.load_state() or {}
            rel_path = raw_state.get("TabState", {}).get("Tab1", {}).get("RawAudioFilePath")
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
        record: TaskRecord | None = None,
    ) -> dict[str, Any]:
        """Persist separated stem buffers back into the workshop state."""
        mgr = self.kernel._require_manager()
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
                ws.fail_separation(str(error), task_id=record.id if record else None)
                return result

            if record is not None:
                self.kernel.tasks.stage(record, "committing")
            track_files = self._persist_separated_tracks(
                wid,
                plugin_name,
                task_id=record.id if record else None,
            )
            ws.complete_separation(track_files, task_id=record.id if record else None)
            return result
        except Exception as e:  # noqa: BLE001
            ws.fail_separation(str(e), task_id=record.id if record else None)
            raise

    def _persist_separated_tracks(
        self,
        wid: str,
        plugin_name: str,
        task_id: str | None = None,
    ) -> dict[str, str]:
        """Write RC stem buffers to workshop cache and return relative paths."""
        mgr = self.kernel._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        orch = self.kernel._require_orchestrator()
        context = orch.get_context(wid)
        stems = context.rc.get_metadata("separated_stems")
        if not stems:
            stems = ["vocals", "drums", "bass", "piano", "guitar", "other"]

        track_files: dict[str, str] = {}

        from src.audio.loader import AudioData, save_audio

        for stem in stems:
            stem_name = str(stem)
            samples, sample_rate = context.rc.get_audio_buffer(stem_name)
            audio_samples = self._normalize_audio_samples_for_save(samples, sample_rate)

            out_path = ws.cache.track_audio_path(
                stem_name,
                f"{task_id or plugin_name}_{stem_name}.wav",
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
    def _normalize_audio_samples_for_save(samples, sample_rate: int = 44100):
        """Validate canonical audio without guessing its channel axis."""
        from src.audio.contracts import as_audio

        return as_audio(samples, sample_rate)

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
        self.kernel._require_orchestrator()
        mgr = self.kernel._require_manager()
        ws = mgr.get(wid)
        if ws is None:
            raise RuntimeError(f"Workshop not found: {wid}")

        record, reused = self.kernel.tasks.admit(
            wid,
            "analysis",
            (stem_name, plugin_name, ws.state.tab_state.tab1.raw_audio_file_path),
            plugin=plugin_name,
            track=stem_name,
        )
        if reused:
            if record.handle is None:
                raise TaskBusyError("Task is being prepared")
            return record.handle
        try:
            ws.upsert_analysis_task(stem_name, plugin_name, task_id=record.id)
            handle = asyncio.create_task(
                self._supervise_analysis(
                    record,
                    stem_name,
                    plugin_name,
                    durations_sec,
                )
            )
            self.kernel.tasks.attach(record, handle)
            handle.add_done_callback(
                lambda finished: self.kernel._reconcile_task_exit(record, finished)
            )
            return handle
        except BaseException as error:
            if ws.state.tab_state.tab3.get(f"{stem_name}::{record.id}") is not None:
                ws.fail_analysis(stem_name, record.id, str(error))
            self.kernel.tasks.finish(record, "failed", str(error))
            raise

    async def _supervise_analysis(
        self,
        record: TaskRecord,
        stem_name: str,
        plugin_name: str,
        durations_sec: float,
    ) -> dict[str, Any]:
        ws = self.kernel._require_manager().get(record.workshop_id)
        try:
            self.kernel.tasks.stage(record, "loading_audio")
            await _await_blocking(
                self._load_workshop_stem_into_rc,
                record.workshop_id,
                stem_name,
            )
            self.kernel.tasks.stage(record, "running_plugin")
            inner_task = self.kernel._require_orchestrator().start_analysis(
                record.workshop_id,
                self.kernel.bus,
                plugin_name=plugin_name,
                stem_name=stem_name,
                durations_sec=durations_sec,
                emit_lifecycle=False,
                task_id=record.id,
            )
            result = await self._finalize_analysis_task(
                record.workshop_id, stem_name, record.id, plugin_name, inner_task, record
            )
            self.kernel.tasks.finish(
                record,
                "failed" if result.get("status") == "failed" else "done",
                result.get("error"),
            )
            return result
        except asyncio.CancelledError:
            try:
                if ws is not None:
                    ws.cancel_analysis(stem_name, record.id)
            finally:
                if self.kernel.orchestrator is not None:
                    self.kernel.orchestrator.get_context(record.workshop_id).rc.clear()
                self.kernel.tasks.finish(record, "cancelled")
            return {"status": "cancelled"}
        except Exception as error:
            task_state = (
                ws.state.tab_state.tab3.get(f"{stem_name}::{record.id}") if ws is not None else None
            )
            try:
                if task_state is not None and task_state.analysis_state == "running":
                    ws.fail_analysis(stem_name, record.id, str(error))
            finally:
                self.kernel.tasks.finish(record, "failed", str(error))
            raise

    async def _finalize_analysis_task(
        self,
        wid: str,
        stem_name: str,
        task_id: str,
        plugin_name: str,
        inner_task,
        record: TaskRecord | None = None,
    ) -> dict[str, Any]:
        """Persist analysis results back into workshop cache + state."""
        mgr = self.kernel._require_manager()
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
            if record is not None:
                self.kernel.tasks.stage(record, "committing")
            abs_path = ws.cache.save_analysis_result(plugin_name, task_id, result_data, ext="json")
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
