"""插件元信息端点。

端点：

* ``GET /api/plugins/separators`` —— 列出可用的分离插件（给 Tab2 下拉列表）
* ``GET /api/plugins/analyzers`` —— 列出可用的分析插件（给 Tab3 下拉列表）

实际工作走 :py:class:`src.kernel.kernel.Kernel.list_*_plugins`。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _kernel(request: Request):
    k = getattr(request.app.state, "kernel", None)
    if k is None:
        raise HTTPException(503, "Kernel 未启动")
    return k


@router.get("/plugins/separators")
def list_separators(request: Request) -> list[dict[str, Any]]:
    """可用分离插件列表（给 Tab2 下拉列表）。"""
    k = _kernel(request)
    try:
        return k.list_separator_plugins()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/plugins/analyzers")
def list_analyzers(request: Request) -> list[dict[str, Any]]:
    """可用分析插件列表（给 Tab3 下拉列表）。"""
    k = _kernel(request)
    try:
        return k.list_analyzer_plugins()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


__all__ = ["router"]
