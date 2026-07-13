"""SSE 事件流端点。

设计:

* 每条 SSE 连接独占一个 :py:meth:`EventBus.subscribe` 返回的 Queue
* 服务端 :py:meth:`generate` 协程: ``await q.get()`` → 构造 SSE 帧 → yield
* **不过滤事件**：所有订阅者收到 kernel 推送的**全部事件**，**过滤在前端按
  ``workshop_id`` 字段做**。这样 EventBus 保持简单（一对多 publish），UI 层负责
  路由到正确的车间连接。

事件格式（每条 SSE message 的 ``data:`` 内容）::

    {
      "type": "separation_done",
      "payload": {...},
      "workshop_id": "abc12345",
      "emitted_at": 1783765628.05
    }
"""

from __future__ import annotations

import json
from queue import Empty, Queue
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """订阅 kernel.bus 的所有事件。

    通过 ``request.app.state.kernel`` 拿 kernel 单例。
    断开（client 关闭连接）自动 unsubscribe。
    """
    kernel = getattr(request.app.state, "kernel", None)
    if kernel is None:
        # 还没有 kernel 时直接返回空流；前端看到 connect error 后会重试
        async def _empty() -> Any:
            if False:
                yield b""  # never executed, makes this a generator

        return StreamingResponse(_empty(), media_type="text/event-stream")

    q: Queue = kernel.bus.subscribe()

    async def generate() -> Any:
        try:
            while True:
                # 检查连接是否断开（client 关闭浏览器 tab 会触发）
                if await request.is_disconnected():
                    break
                # 非阻塞 poll，~ 0.3 秒超时，让 is_disconnected 及时检查
                try:
                    ev = q.get(timeout=0.3)
                except Empty:
                    continue
                frame = json.dumps(
                    {
                        "type": ev.type,
                        "payload": ev.payload,
                        "workshop_id": ev.workshop_id,
                        "emitted_at": ev.emitted_at,
                    },
                    ensure_ascii=False,
                )
                # SSE 协议要求每个 message 以 \n\n 结尾
                yield f"data: {frame}\n\n"
        finally:
            kernel.bus.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 用，防缓冲
            "Connection": "keep-alive",
        },
    )


# Keep asyncio import here even if unused locally (for type comments / future)
__all__ = ["router"]  # noqa: F401
