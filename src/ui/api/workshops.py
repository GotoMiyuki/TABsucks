"""车间 CRUD 路由。

依赖 :py:class:`src.kernel.kernel.Kernel` 实例（通过 ``request.app.state.kernel`` 拿）。
错误响应统一格式：``{"error": "<msg>"}`` + 适当 HTTP status。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


# ---------------------------------------------------------------------------
# Body 模型（pydantic 校验）
# ---------------------------------------------------------------------------


class CreateWorkshopRequest(BaseModel):
    """POST /api/workshops body."""

    name: str = Field(default="New Workshop", min_length=1, max_length=200)


class RenameWorkshopRequest(BaseModel):
    """PUT /api/workshops/<wid> body。"""

    name: str = Field(min_length=1, max_length=200)


class DeleteWorkshopRequest(BaseModel):
    """DELETE /api/workshops/<wid> body（可选 ``keep_state``）。"""

    keep_state: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kernel(request: Request):
    """从 FastAPI app state 取 Kernel 实例。"""
    kernel = getattr(request.app.state, "kernel", None)
    if kernel is None:
        raise HTTPException(500, "Kernel 未注入")
    return kernel


def _err(status: int, msg: str) -> None:
    """统一错误格式。"""
    raise HTTPException(status_code=status, detail={"error": msg})


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/workshops")
def list_workshops(request: Request) -> list[dict[str, Any]]:
    """所有车间（含已关闭的）。

    返回字段：``id`` / ``name`` / ``last_tab`` / ``active``（是否当前激活）
    """
    kernel = _kernel(request)
    active_id = kernel.manager.active_id() if kernel.manager else None
    result: list[dict[str, Any]] = []
    if kernel.manager is None:
        return result
    for ws in kernel.manager.list_workshops():
        result.append(
            {
                "id": ws.id,
                "name": ws.name,
                "last_tab": ws.last_tab,
                "active": active_id == ws.id,
            }
        )
    return result


@router.post("/workshops", status_code=201)
def create_workshop(req: CreateWorkshopRequest, request: Request) -> dict[str, Any]:
    """创建新车间。会 deactivate 任何当前 active 车间。"""
    kernel = _kernel(request)
    if kernel.manager is None:
        _err(503, "Kernel 未 boot")
    info = kernel.create_workshop(name=req.name)
    return {"id": info["id"], "name": info["name"], "last_tab": info["last_tab"]}


@router.get("/workshops/{wid}")
def get_workshop(wid: str, request: Request) -> dict[str, Any]:
    """车间完整 state.json（dict）。"""
    kernel = _kernel(request)
    if kernel.manager is None:
        _err(503, "Kernel 未 boot")
    state = kernel.get_state(wid)
    if state is None:
        _err(404, f"车间 {wid} 不存在")
    return state


@router.put("/workshops/{wid}")
def rename_workshop(
    wid: str, req: RenameWorkshopRequest, request: Request
) -> dict[str, Any]:
    """重命名车间。"""
    kernel = _kernel(request)
    if kernel.manager is None:
        _err(503, "Kernel 未 boot")
    if not kernel.rename_workshop(wid, req.name):
        _err(404, f"车间 {wid} 不存在")
    return {"ok": True, "name": req.name}


@router.delete("/workshops/{wid}")
def delete_workshop(
    wid: str,
    request: Request,
    keep_state: bool = False,
) -> dict[str, Any]:
    """永久删除车间（内存 + 磁盘）。

    可选 ``?keep_state=true`` 把 ``state.json`` 备份到 ``recycle_bin/`` 再删。
    """
    kernel = _kernel(request)
    if kernel.manager is None:
        _err(503, "Kernel 未 boot")
    if not kernel.delete_workshop(wid, keep_state=keep_state):
        _err(404, f"车间 {wid} 不存在或已被删除")
    return {"ok": True}


@router.post("/workshops/{wid}/close")
def close_workshop(wid: str, request: Request) -> dict[str, Any]:
    """关闭车间 = deactivate。

    * MusicWorkshop 实例仍留内存（每个 < 2KB，可忽略）
    * 磁盘数据保留
    * ``active_id`` 若是本车间，置 None → 欢迎页
    """
    kernel = _kernel(request)
    if kernel.manager is None:
        _err(503, "Kernel 未 boot")
    if not kernel.close_workshop(wid):
        _err(404, f"车间 {wid} 不存在")
    return {"ok": True, "active_id": kernel.manager.active_id()}


@router.post("/workshops/{wid}/switch")
def switch_workshop(wid: str, request: Request) -> dict[str, Any]:
    """切换为 active（如已激活则 noop）。

    * 旧 active 走 close（save + stop autosave）
    * 新 active resume_autosave 启动后台线程
    """
    kernel = _kernel(request)
    if kernel.manager is None:
        _err(503, "Kernel 未 boot")
    if not kernel.switch_workshop(wid):
        _err(404, f"车间 {wid} 不存在")
    return {"ok": True, "active_id": wid}


@router.get("/workshops-active")
def get_active(request: Request) -> dict[str, Any]:
    """当前 active 车间 id（null 表示在欢迎页）。"""
    kernel = _kernel(request)
    if kernel.manager is None:
        return {"active_id": None}
    return {"active_id": kernel.manager.active_id()}


__all__ = ["router"]
