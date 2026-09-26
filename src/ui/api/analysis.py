"""Analysis/separation commands and aggregate workshop workflow router."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.kernel.core.task_service import TaskBusyError

from .dependencies import _err, _kernel
from .media import router as media_router
from .uploads import router as uploads_router

router = APIRouter()
router.include_router(uploads_router)
router.include_router(media_router)


class SeparateRequest(BaseModel):
    model: str = "separation_bs_roformer"
    device: Literal["cpu", "gpu"] = "gpu"


class AnalyzeRequest(BaseModel):
    track: str
    plugin: str = "chord_ismir2019"


@router.post("/workshops/{wid}/separate")
async def trigger_separation(
    wid: str,
    req: SeparateRequest,
    request: Request,
) -> dict:
    """**真实现**：调 :py:meth:`Kernel.start_separation_task` 启动。

    进度经 EventBus 推到 SSE，前端订阅 ``separation_progress`` / ``separation_done`` /
    ``separation_failed``。返回 ``{"ok": true, "task": "<plugin>"}`` 即可，
    不阻塞 request。
    """
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    # 由 Orchestrator.start_separation 异步 emit "separation_started/progress/done"
    try:
        kernel.start_separation_task(
            wid,
            plugin_name=req.model,
            compute_device=req.device,
            durations_sec=3.0,
        )
    except TaskBusyError as e:
        _err(409, str(e))
    except Exception as e:  # noqa: BLE001
        _err(400, f"启动分离失败: {e}")
    # 不 await — 让 FastAPI BackgroundTasks 的协程跑完
    record = kernel.tasks.active(wid)
    return {
        "ok": True,
        "task": req.model,
        "task_id": record.id if record else None,
        "status": record.status if record else "unknown",
    }


# ---------------------------------------------------------------------------
# 分析（**真实现**，替换点 B —— 接 Kernel.start_analysis_task）
# ---------------------------------------------------------------------------


@router.post("/workshops/{wid}/analyze")
async def trigger_analysis(
    wid: str,
    req: AnalyzeRequest,
    request: Request,
) -> dict:
    """**真实现**：调 :py:meth:`Kernel.start_analysis_task`。

    事件经 EventBus → SSE：``analysis_started / progress / done / failed``。
    """
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    try:
        kernel.start_analysis_task(
            wid,
            plugin_name=req.plugin,
            stem_name=req.track,
            durations_sec=1.5,
        )
    except TaskBusyError as error:
        _err(409, str(error))
    record = kernel.tasks.active(wid)
    return {
        "ok": True,
        "task": req.plugin,
        "task_id": record.id if record else None,
        "status": record.status if record else "unknown",
    }


@router.get("/workshops/{wid}/analysis-results")
def get_analysis_results(wid: str, request: Request) -> dict:
    """Return the latest persisted analysis result for each track."""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid)
    if ws is None:
        _err(404, f"Workshop {wid} not found")

    results: dict[str, dict] = {}
    result_plugins: dict[str, str] = {}
    for key, task_state in ws.state.tab_state.tab3.items():
        if task_state.analysis_state != "done" or task_state.analysis_result_path is None:
            continue

        track_name = key.split("::", 1)[0]
        try:
            result_path = ws.cache.to_absolute(task_state.analysis_result_path)
            result_data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue

        if isinstance(result_data, list):
            result_data = {"chords": result_data}
        if isinstance(result_data, dict):
            results[track_name] = result_data
            if task_state.analysis_tool_name:
                result_plugins[track_name] = task_state.analysis_tool_name

    return {
        "ok": True,
        "results": results,
        "result_plugins": result_plugins,
    }
