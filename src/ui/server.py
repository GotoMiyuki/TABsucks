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
from src.ui.api.plugins import router as plugins_router
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
    app.include_router(plugins_router, prefix="/api", tags=["plugins"])

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """首页（HTML shell，前端 spa）。"""
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app


__all__ = ["make_app", "STATIC_DIR"]


def build_default_app(*, cache_root=None, autosave: bool = True) -> FastAPI:
    """CLI / uvicorn import path 用的默认构造: 实例化 Kernel + boot + make_app."""
    k = Kernel(cache_root=cache_root, autosave=autosave)
    k.boot()
    return make_app(k)


#: ``uvicorn --reload`` 必须用 import string.
#:
#: 关键点: 不在模块顶层放 ``app = None``, 否则 reload worker re-import
#: 本模块时 ``app = None`` 会被重新执行一次, uvicorn reload 后第一时间拿到
#: None 报错 "NoneType is not callable".
#:
#: 改方案: 完全不暴露模块顶层 ``app`` 变量, 由 :func:`__getattr__` 接管,
#: 任何对 ``src.ui.server.app`` 的访问都通过 _install_default_app() 懒构造,
#: 缓存到 ``_app_instance``. 整个生命周期 ``app`` 只 build 一次.
_app_instance: FastAPI | None = None  # 模块级缓存（reload-safe）


def _install_default_app() -> FastAPI:
    """CLI 在 uvicorn.run 之前构造并缓存 app。"""
    global _app_instance
    if _app_instance is None:
        _app_instance = build_default_app()
    return _app_instance


__all__ = ["make_app", "build_default_app", "_install_default_app", "STATIC_DIR"]  # noqa: F822 — app 暴露通过 __getattr__


def __getattr__(name: str):  # noqa: D401
    """模块级懒属性: ``app`` 首次访问时构造, 解决 reload worker re-import 时
    模块顶层 ``app = None`` 被重新执行的问题, 统一通过 __getattr__ 触发 build。
    """
    if name == "app":
        return _install_default_app()
    raise AttributeError(f"module 'src.ui.server' has no attribute {name!r}")
