"""Kernel 编排层：把 PM/AE/RC 装配起来 + 让 Workshop 可以发起任务。

依据：
* 会议 §2 PM/AE/RC 协作
* :py:mod:`src.kernel.core.plugin_manager.PluginManager`
* :py:mod:`src.kernel.core.analysis_engine.AnalysisEngine`
* 本仓库的 ``Plugin`` ABC（：py:meth:`name/version/execute(rc, **kwargs)`）

模块功能：

1. :py:class:`Orchestrator` —— 实例化 PM 与 RC，注册【范例 plugin】（MVP）
2. :py:meth:`Orchestrator.start_separation`：发起分离任务，跑在 BackgroundTasks 里，
   通过 :py:func:`make_progress_callback` 把进度推到 :py:class:`EventBus`。
3. :py:meth:`Orchestrator.start_analysis`：分析同上。

MVP 阶段，所有 plugin 都是 :py:mod:`src.plugins._example_separator` / ``_example_analyzer``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from src.kernel.core.analysis_engine import AnalysisEngine
from src.kernel.core.plugin_manager import PluginManager
from src.kernel.core.resource_controller import ResourceController

logger = logging.getLogger(__name__)


def make_progress_callback(
    bus,
    wid: str,
    event_type: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Callable[[float], None]:
    """构造 plugin ``progress_callback``：把 0~1 进度推到 :py:class:`EventBus`。

    返回的闭包可安全地传给 ``plugin.execute(rc, progress_callback=cb)``。
    extra：每个 emit 携带的额外字段（如 ``{"track": "vocals"}``）。
    """
    payload_base: dict[str, Any] = dict(extra or {})

    def cb(progress: float) -> None:
        try:
            payload = {"progress": float(progress)}
            payload.update(payload_base)
            bus.emit(wid, event_type, payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("progress_callback 推送异常: %s", e)

    return cb


async def call_plugin_execute_async(
    plugin,
    rc,
    *,
    progress_interval_sec: float = 0.03,
    durations_sec: float = 3.0,
    progress_callback=None,
) -> dict[str, Any]:
    """异步包装同步 plugin：把 ``time.sleep`` 改成 ``asyncio.sleep``。

    如果 plugin 自己有 ``run_async`` 协程（看 _example_separator.run_async），优先用之。
    否则开一个线程跑同步版本。``progress_callback`` 同时被传给 ``run_async`` /
    同步 ``execute``，由 plugin 决定何时调用。
    """
    # 优先 plugin.run_async
    run_async = getattr(plugin, "run_async", None)
    if run_async is not None and asyncio.iscoroutinefunction(run_async):
        if progress_callback is not None:
            return await run_async(
                rc,
                durations_sec=durations_sec,
                progress_callback=progress_callback,
            )
        return await run_async(rc, durations_sec=durations_sec)

    # 回退：在默认 executor 跑同步 execute
    loop = asyncio.get_running_loop()

    def sync_run():
        kwargs: dict[str, Any] = {"durations_sec": 0.0}
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback
        return plugin.execute(rc, **kwargs)

    return await loop.run_in_executor(None, sync_run)


class Orchestrator:
    """进程级 PM/AE 编排者。

    :py:meth:`__init__` 时：

    * 实例化 :py:class:`ResourceController`
    * 实例化 :py:class:`PluginManager`
    * 注册 :py:mod:`src.plugins._example_separator` 和 :py:mod:`_example_analyzer`
    * 实例化 :py:class:`AnalysisEngine`

    未来可以接入其他真实 plugin（如 BS-RoFormer 通过 manifest 扫盘）。

    Attributes:
        rc: 资源控制器。
        pm: 插件管理器。
        ae: 分析引擎。
    """

    #: 默认 plugins（MVP 范例）
    DEFAULT_PLUGINS: tuple[str, ...] = (
        "src.plugins._example_separator:ExampleSeparatorPlugin",
        "src.plugins._example_analyzer:ExampleAnalyzerPlugin",
    )

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

    # ---------- 插件注册 ----------

    def _register_default_plugins(self) -> None:
        """注册 MVP 阶段的两个范例 plugin。

        用 ``importlib`` + ``:`` 形式定位（与现有 PM 风格一致）。
        """
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

    # ---------- 任务启动 ----------

    def start_separation(
        self,
        wid: str,
        bus,
        *,
        plugin_name: str = "example_separator",
        audio_samples=None,
        sample_rate: int = 22050,
        progress_event: str = "separation_progress",
        durations_sec: float = 3.0,
    ) -> asyncio.Task:
        """异步启动分离任务。

        步骤：
        1. 把 audio_samples 写到 RC buffer "raw" + metadata sample_rate
        2. 构造 progress_callback（推到 bus）
        3. 异步执行 plugin.execute，bus.emit "separation_started"/"progress"/"done"/"failed"
        4. 返回 asyncio.Task 让调用方 await

        Returns:
            asyncio.Task，结果是 plugin 返回的 dict。
        """
        import numpy as np

        if audio_samples is not None:
            arr = np.asarray(audio_samples, dtype=np.float32)
            self.rc.set_buffer("raw", arr)
            self.rc.set_metadata("sample_rate", int(sample_rate))

        async def _run() -> dict[str, Any]:
            bus.emit(wid, "separation_started", {"plugin": plugin_name})

            cb = make_progress_callback(bus, wid, progress_event)

            plugin = self.pm.get(plugin_name)
            if plugin is None:
                bus.emit(
                    wid,
                    "separation_failed",
                    {"plugin": plugin_name, "error": f"plugin 不存在: {plugin_name}"},
                )
                return {"status": "failed"}

            try:
                result = await call_plugin_execute_async(
                    plugin,
                    self.rc,
                    durations_sec=durations_sec,
                    progress_callback=cb,
                )
                stems = result.get("data", {}).get("stems", []) if isinstance(result, dict) else []
                bus.emit(wid, "separation_done", {"plugin": plugin_name, "stems": stems})
                return result
            except Exception as e:  # noqa: BLE001
                bus.emit(
                    wid,
                    "separation_failed",
                    {"plugin": plugin_name, "error": str(e)},
                )
                return {"status": "failed", "error": str(e)}

        # 在调用方 loop 跑（FastAPI BackgroundTasks 通常提供 loop）
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 测试 / 同步上下文：开新 loop 跑
            loop = asyncio.new_event_loop()
        return loop.create_task(_run())

    def start_analysis(
        self,
        wid: str,
        bus,
        *,
        plugin_name: str = "example_analyzer",
        stem_name: str = "vocals",
        progress_event: str = "analysis_progress",
        durations_sec: float = 1.5,
    ) -> asyncio.Task:
        """异步启动分析。"""
        async def _run() -> dict[str, Any]:
            bus.emit(
                wid,
                "analysis_started",
                {"plugin": plugin_name, "track": stem_name},
            )

            cb = make_progress_callback(
                bus,
                wid,
                progress_event,
                extra={"track": stem_name},
            )
            plugin = self.pm.get(plugin_name)
            if plugin is None:
                bus.emit(
                    wid,
                    "analysis_failed",
                    {"plugin": plugin_name, "track": stem_name, "error": "plugin 不存在"},
                )
                return {"status": "failed"}

            try:
                # 给 plugin 喂 RC 中已经有的 stem buffer（无需再次 set）
                result = await call_plugin_execute_async(
                    plugin,
                    self.rc,
                    durations_sec=durations_sec,
                    progress_callback=cb,
                )
                bus.emit(
                    wid,
                    "analysis_done",
                    {
                        "plugin": plugin_name,
                        "track": stem_name,
                        "result": result.get("data", {}),
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

    # ---------- 查询 ----------

    def list_separator_plugins(self) -> list[dict[str, Any]]:
        """列出可用的分离 plugin 描述（给 UI 下拉列表）。

        MVP 阶段返回纯手动列表，未来可走 manifest 扫盘。
        """
        return [
            {
                "name": "example_separator",
                "display_name": "Mock 6-Stem Separator (范例)",
                "version": "0.0.1",
                "mock": True,
            }
        ]

    def list_analyzer_plugins(self) -> list[dict[str, Any]]:
        """列出可用的分析 plugin 描述。"""
        return [
            {
                "name": "example_analyzer",
                "display_name": "Mock Analyzer (范例)",
                "version": "0.0.1",
                "mock": True,
                "input_kind": "stem",
            }
        ]


__all__ = ["Orchestrator", "make_progress_callback", "call_plugin_execute_async"]
