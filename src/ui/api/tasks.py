"""Query and cancellation routes for process-local workshop tasks."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from src.kernel.core.task_service import TaskBusyError

router = APIRouter()


def _service(request: Request):
    kernel = getattr(request.app.state, "kernel", None)
    if kernel is None or not hasattr(kernel, "tasks"):
        raise HTTPException(503, "Task service is unavailable")
    return kernel.tasks


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    record = _service(request).get(task_id)
    if record is None:
        raise HTTPException(404, "Task not found")
    return record.snapshot()


@router.get("/workshops/{wid}/tasks")
def list_tasks(wid: str, request: Request) -> list[dict]:
    return _service(request).list_workshop(wid)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict:
    try:
        record = _service(request).cancel(task_id)
    except TaskBusyError as error:
        raise HTTPException(409, str(error)) from error
    if record is None:
        raise HTTPException(404, "Task not found")
    return record.snapshot()


__all__ = ["router"]
