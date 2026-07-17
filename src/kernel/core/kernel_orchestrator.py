"""Kernel orchestration layer: ResourceController + PluginManager + AnalysisEngine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from src.kernel.core.analysis_engine import AnalysisEngine
from src.kernel.core.plugin_manager import PluginManager, PluginManagerError
from src.kernel.core.resource_controller import ResourceController

logger = logging.getLogger(__name__)


def make_progress_callback(
    bus,
    wid: str,
    event_type: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Callable[[float], None]:
    """Build a plugin progress callback that emits to EventBus."""
    payload_base: dict[str, Any] = dict(extra or {})

    def cb(progress: float) -> None:
        try:
            payload = {"progress": float(progress)}
            payload.update(payload_base)
            bus.emit(wid, event_type, payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("progress callback emit failed: %s", e)

    return cb


def emit_progress_event(
    bus,
    wid: str,
    event_type: str,
    progress: float,
    **extra: Any,
) -> None:
    """Emit one progress event with optional structured context."""
    payload = {"progress": float(progress)}
    payload.update(extra)
    bus.emit(wid, event_type, payload)


def _normalise_compute_device(device: str) -> str:
    value = str(device).strip().lower()
    if value not in {"cpu", "gpu"}:
        raise PluginManagerError(f"Unsupported compute device: {device}")
    return value


async def call_plugin_execute_async(
    plugin,
    rc,
    *,
    progress_interval_sec: float = 0.5,
    durations_sec: float = 3.0,
    progress_callback=None,
    **extra_kwargs,
) -> dict[str, Any]:
    """Run a plugin from async orchestration code.

    If the plugin instance exposes an async ``run_async`` method, use it.
    Otherwise execute the synchronous ``execute`` method in the default executor.

    Extra keyword arguments (e.g. ``stem_name``) are forwarded to the plugin.
    """
    run_async = getattr(plugin, "run_async", None)
    if run_async is not None and asyncio.iscoroutinefunction(run_async):
        kwargs: dict[str, Any] = {"durations_sec": durations_sec, **extra_kwargs}
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        return await run_async(rc, **kwargs)

    loop = asyncio.get_running_loop()
    last_progress = 0.0

    def emit_progress(progress: float) -> None:
        nonlocal last_progress
        if progress_callback is None:
            return
        bounded = max(0.0, min(float(progress), 0.99))
        if bounded < last_progress:
            bounded = last_progress
        last_progress = bounded
        progress_callback(bounded)

    def sync_run():
        kwargs: dict[str, Any] = {"durations_sec": durations_sec, **extra_kwargs}
        if progress_callback is not None:
            kwargs["progress_callback"] = emit_progress
        return plugin.execute(rc, **kwargs)

    future = loop.run_in_executor(None, sync_run)
    if progress_callback is None or bool(getattr(plugin, "reports_progress", False)):
        return await future

    heartbeat_interval = max(float(progress_interval_sec), 0.05)
    while not future.done():
        done, _ = await asyncio.wait({future}, timeout=heartbeat_interval)
        if done:
            break
        if last_progress < 0.95:
            emit_progress(min(0.95, last_progress + 0.02))

    return await future


class Orchestrator:
    """Process-level coordinator for plugin execution."""

    DEFAULT_PLUGINS: tuple[str, ...] = (
        "src.plugins._example_separator:ExampleSeparatorPlugin",
        "src.plugins._example_analyzer:ExampleAnalyzerPlugin",
    )

    SEPARATOR_ALIASES: dict[str, str] = {
        "BS-RoFormer": "example_separator",
        "BS-RoFormer-SW": "example_separator",
        "BS-Roformer-SW": "example_separator",
        "BS-Roformer-SW.ckpt": "separation_bs_roformer",
        "BS-Roformer-SW.yaml": "separation_bs_roformer",
    }

    def __init__(
        self,
        *,
        rc: ResourceController | None = None,
        pm: PluginManager | None = None,
        register_examples: bool = True,
    ) -> None:
        self.rc: ResourceController = rc or ResourceController()
        self.pm: PluginManager = pm or PluginManager(self.rc)
        if register_examples:
            self._register_default_plugins()
        self.ae: AnalysisEngine = AnalysisEngine(self.rc, self.pm)

    def _register_default_plugins(self) -> None:
        """Register MVP example plugins explicitly."""
        import importlib

        for path in self.DEFAULT_PLUGINS:
            module_path, _, class_name = path.partition(":")
            try:
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                self.pm.register(cls())
                logger.info("Registered plugin: %s", class_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to register %s: %s", path, e)

    def _resolve_separator_name(self, plugin_name: str) -> str:
        """Accept old UI model labels while moving toward plugin ids."""
        if self.pm.get(plugin_name) is not None or self.pm.get_manifest(plugin_name) is not None:
            return plugin_name
        return self.SEPARATOR_ALIASES.get(plugin_name, plugin_name)

    def _ensure_plugin(self, plugin_name: str, config: dict[str, Any] | None = None):
        """Return a registered plugin or lazily instantiate a manifest plugin."""
        try:
            return self.pm.ensure_plugin(plugin_name, config=config)
        except PluginManagerError:
            raise
        except Exception as e:  # noqa: BLE001
            raise PluginManagerError(str(e)) from e

    def start_separation(
        self,
        wid: str,
        bus,
        *,
        plugin_name: str = "example_separator",
        audio_samples=None,
        sample_rate: int = 22050,
        compute_device: str = "gpu",
        progress_event: str = "separation_progress",
        durations_sec: float = 3.0,
    ) -> asyncio.Task:
        """Start a separation plugin task and emit lifecycle/progress events."""
        import numpy as np

        if audio_samples is not None:
            arr = np.asarray(audio_samples, dtype=np.float32)
            self.rc.set_buffer("raw", arr)
            self.rc.set_metadata("sample_rate", int(sample_rate))

        async def _run() -> dict[str, Any]:
            resolved_plugin = self._resolve_separator_name(plugin_name)
            requested_device = _normalise_compute_device(compute_device)
            try:
                plugin = self._ensure_plugin(resolved_plugin)
                if plugin is None:
                    raise PluginManagerError(f"plugin not found: {resolved_plugin}")
                effective_device = self._resolve_separation_device(
                    resolved_plugin,
                    requested_device,
                )
            except PluginManagerError as error:
                bus.emit(
                    wid,
                    "separation_failed",
                    {
                        "plugin": resolved_plugin,
                        "requested_device": requested_device,
                        "error": str(error),
                    },
                )
                return {"status": "failed", "error": str(error)}

            bus.emit(
                wid,
                "separation_started",
                {
                    "plugin": resolved_plugin,
                    "requested_device": requested_device,
                    "effective_device": effective_device,
                },
            )
            cb = make_progress_callback(bus, wid, progress_event)
            emit_progress_event(
                bus,
                wid,
                progress_event,
                0.01,
                plugin=resolved_plugin,
                stage="loading_plugin",
            )

            is_manifest_plugin = self.pm.get_manifest(resolved_plugin) is not None
            vram_reserved = False
            try:
                if is_manifest_plugin and effective_device == "gpu":
                    emit_progress_event(
                        bus,
                        wid,
                        progress_event,
                        0.03,
                        plugin=resolved_plugin,
                        stage="preparing_vram",
                    )
                    vram = self.pm.prepare_vram(resolved_plugin)
                    if not vram.get("ready", False):
                        raise PluginManagerError(vram.get("message", "VRAM is not ready"))
                    vram_reserved = True

                emit_progress_event(
                    bus,
                    wid,
                    progress_event,
                    0.05,
                    plugin=resolved_plugin,
                    stage="running_plugin",
                )
                result = await call_plugin_execute_async(
                    plugin,
                    self.rc,
                    durations_sec=durations_sec,
                    progress_callback=cb,
                    compute_device=effective_device,
                )
                cb(1.0)
                stems = result.get("data", {}).get("stems", []) if isinstance(result, dict) else []
                bus.emit(
                    wid,
                    "separation_done",
                    {
                        "plugin": resolved_plugin,
                        "stems": stems,
                        "requested_device": requested_device,
                        "effective_device": effective_device,
                    },
                )
                return result
            except Exception as e:  # noqa: BLE001
                bus.emit(
                    wid,
                    "separation_failed",
                    {
                        "plugin": resolved_plugin,
                        "requested_device": requested_device,
                        "effective_device": effective_device,
                        "error": str(e),
                    },
                )
                return {"status": "failed", "error": str(e)}
            finally:
                if is_manifest_plugin and vram_reserved:
                    self.rc.release_vram(resolved_plugin)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.create_task(_run())

    def _resolve_separation_device(
        self,
        plugin_name: str,
        requested_device: str,
    ) -> str:
        """Resolve a device request against plugin capability and local hardware."""
        manifest = self.pm.get_manifest(plugin_name)
        supported = {"cpu"}
        if manifest is not None:
            declared = manifest.get("supported_devices", ["cpu"])
            if isinstance(declared, list):
                supported = {
                    str(item).strip().lower()
                    for item in declared
                    if str(item).strip().lower() in {"cpu", "gpu"}
                } or {"cpu"}

        if requested_device in supported:
            effective_device = requested_device
        elif "cpu" in supported:
            effective_device = "cpu"
        else:
            effective_device = "gpu"

        if effective_device == "gpu":
            gpu_info = self.rc.get_gpu_info()
            if not gpu_info.get("cuda_available", False):
                raise PluginManagerError(
                    f"GPU was requested for {plugin_name}, but CUDA is unavailable."
                )
        return effective_device

    def start_analysis(
        self,
        wid: str,
        bus,
        *,
        plugin_name: str = "example_analyzer",
        stem_name: str = "vocals",
        progress_event: str = "analysis_progress",
        durations_sec: float = 1.5,
        emit_done_event: bool = True,
    ) -> asyncio.Task:
        """Start an analyzer plugin task and emit lifecycle/progress events."""

        async def _run() -> dict[str, Any]:
            bus.emit(wid, "analysis_started", {"plugin": plugin_name, "track": stem_name})
            cb = make_progress_callback(
                bus,
                wid,
                progress_event,
                extra={"track": stem_name},
            )

            try:
                plugin = self._ensure_plugin(plugin_name)
            except PluginManagerError as e:
                bus.emit(
                    wid,
                    "analysis_failed",
                    {"plugin": plugin_name, "track": stem_name, "error": str(e)},
                )
                return {"status": "failed", "error": str(e)}

            if plugin is None:
                message = f"plugin not found: {plugin_name}"
                bus.emit(
                    wid,
                    "analysis_failed",
                    {"plugin": plugin_name, "track": stem_name, "error": message},
                )
                return {"status": "failed", "error": message}

            try:
                result = await call_plugin_execute_async(
                    plugin,
                    self.rc,
                    durations_sec=durations_sec,
                    progress_callback=cb,
                    stem_name=stem_name,
                )
                if emit_done_event:
                    result_data = result.get("data", {})
                    if isinstance(result_data, list):
                        result_data = {"chords": result_data}
                    bus.emit(
                        wid,
                        "analysis_done",
                        {
                            "plugin": plugin_name,
                            "track": stem_name,
                            "result": result_data,
                        },
                    )
                return result
            except Exception as e:  # noqa: BLE001
                bus.emit(
                    wid,
                    "analysis_failed",
                    {"plugin": plugin_name, "track": stem_name, "error": str(e)},
                )
                return {"status": "failed", "error": str(e)}

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.create_task(_run())

    def list_separator_plugins(self) -> list[dict[str, Any]]:
        """List real manifest-backed separators plus the example fallback."""
        plugins = self.pm.get_available_plugins(phase="separation")
        existing = {p.get("name") for p in plugins}
        if "example_separator" not in existing and self.pm.get("example_separator") is not None:
            plugins.append(
                {
                    "name": "example_separator",
                    "display_name": "Mock 6-Stem Separator (example)",
                    "version": "0.0.1",
                    "mock": True,
                }
            )
        return plugins

    def list_analyzer_plugins(self) -> list[dict[str, Any]]:
        """List analyzer plugins available to Tab3 (from manifest + example fallback)."""
        plugins: list[dict[str, Any]] = []
        plugins.extend(self.pm.get_available_plugins(phase="post-separation"))
        plugins.extend(self.pm.get_available_plugins(phase="pre-separation"))
        existing = {p.get("name") for p in plugins}
        if "example_analyzer" not in existing and self.pm.get("example_analyzer") is not None:
            plugins.append(
                {
                    "name": "example_analyzer",
                    "display_name": "Mock Analyzer (example)",
                    "version": "0.0.1",
                    "mock": True,
                    "input_kind": "stem",
                }
            )
        return plugins


__all__ = ["Orchestrator", "make_progress_callback", "call_plugin_execute_async"]
