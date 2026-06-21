"""车间 CRUD 路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.kernel.core.workspace import WorkspaceManager

router = APIRouter(tags=["workshops"])

# 全局单例
wm = WorkspaceManager()
# 启动时创建一个默认车间
wm.create("Demo Workshop")


class CreateWorkshopRequest(BaseModel):
    name: str = "新建车间"


class UpdateWorkshopRequest(BaseModel):
    name: str | None = None


@router.get("/workshops")
async def list_workshops():
    return [
        {"id": ws.id, "name": ws.name, "audioPath": ws.audio_path}
        for ws in wm.list_workspaces()
    ]


@router.post("/workshops")
async def create_workshop(req: CreateWorkshopRequest):
    ws = wm.create(req.name)
    return {"id": ws.id, "name": ws.name}


@router.get("/workshops/{wid}")
async def get_workshop(wid: str):
    ws = wm._workspaces.get(wid)
    if not ws:
        raise HTTPException(404, "车间不存在")
    return ws.to_dict()


@router.put("/workshops/{wid}")
async def update_workshop(wid: str, req: UpdateWorkshopRequest):
    ws = wm._workspaces.get(wid)
    if not ws:
        raise HTTPException(404, "车间不存在")
    if req.name is not None:
        ws.name = req.name
    return ws.to_dict()


@router.delete("/workshops/{wid}")
async def delete_workshop(wid: str):
    if not wm.close(wid):
        raise HTTPException(404, "车间不存在")
    return {"ok": True}


@router.post("/workshops/{wid}/switch")
async def switch_workshop(wid: str):
    if not wm.switch_to(wid):
        raise HTTPException(404, "车间不存在")
    return {"ok": True}
