"""TABsucks FastAPI 服务器工厂。

设计要点：

* 用 :py:func:`make_app` 工厂函数接收 :py:class:`src.kernel.kernel.Kernel` 实例，
  不依赖模块级单例。便于单测时给每个 test 注入 FakeKernel。
* 所有 router 通过 ``request.app.state.kernel`` 拿 Kernel（FastAPI 推荐做法）。
* EventBus 是 Kernel.bus，所有 SSE 订阅走它（不分 workshop，因为 Filter 在前端做）。

启动::

    from src.kernel.kernel import Kernel
    from src.ui.server import make_app
    import uvicorn

    kernel = Kernel(cache_root=Path("cache"))
    kernel.boot()
    uvicorn.run(make_app(kernel), host="127.0.0.1", port=8000)

单测::

    from fastapi.testclient import TestClient
    from src.ui.server import make_app
    kernel = FakeKernel()
    client = TestClient(make_app(kernel))
    resp = client.get("/api/workshops")
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.kernel.kernel import Kernel
from src.ui.api.analysis import router as analysis_router
from src.ui.api.events import router as events_router
from src.ui.api.workshops import router as workshops_router

# 与本文件同目录的 static
STATIC_DIR: Path = Path(__file__).parent / "static"


def make_app(kernel: Kernel) -> FastAPI:
    """构造一个挂在给定 kernel 上的 FastAPI app。

    Args:
        kernel: 已 :py:meth:`Kernel.boot` 过的实例。

    Returns:
        :py:class:`fastapi.FastAPI` 应用。
    """
    app = FastAPI(title="TABsucks", version="0.2.0")
    app.state.kernel = kernel

    # 静态资源
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # API 路由
    app.include_router(workshops_router, prefix="/api", tags=["workshops"])
    app.include_router(events_router, prefix="/api", tags=["events"])
    app.include_router(analysis_router, prefix="/api", tags=["analysis"])

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """首页（HTML shell，前端 spa）。"""
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app


__all__ = ["make_app", "STATIC_DIR"]
