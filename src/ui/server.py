"""FastAPI 服务器入口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.ui.api.workshops import router as workshops_router
from src.ui.api.analysis import router as analysis_router
from src.ui.api.events import router as events_router

app = FastAPI(title="TABsucks", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(workshops_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(events_router, prefix="/api")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
