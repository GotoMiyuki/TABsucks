"""SSE 事件推送端点。"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.kernel.core.event_bus import bus

router = APIRouter(tags=["events"])


@router.get("/workshops/{wid}/events")
async def workshop_events(wid: str):
    q = bus.subscribe(wid)

    async def generate():
        try:
            while True:
                event = await q.get()
                data = json.dumps({"type": event.type, "payload": event.payload}, ensure_ascii=False)
                yield f"data: {data}\n\n"
        finally:
            bus.unsubscribe(wid, q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
