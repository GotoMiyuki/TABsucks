"""Shared HTTP dependencies and error envelope."""

from fastapi import HTTPException, Request


def _kernel(request: Request):
    kernel = getattr(request.app.state, "kernel", None)
    if kernel is None:
        raise HTTPException(503, "Kernel 未注入 / 未 boot")
    if kernel.manager is None:
        raise HTTPException(503, "Kernel.boot 未被调用")
    return kernel


def _bus(request: Request):
    return request.app.state.kernel.bus


def _err(status: int, msg: str) -> None:
    raise HTTPException(status_code=status, detail={"error": msg})
