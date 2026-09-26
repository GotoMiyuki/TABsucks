"""Chunked local uploads and supervised URL downloads."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from src.kernel.core.async_workers import await_blocking as _run_blocking
from src.kernel.core.async_workers import await_committing as _run_committing
from src.kernel.core.task_service import TaskBusyError

from .dependencies import _bus, _err, _kernel

router = APIRouter()
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = int(os.environ.get("TABSUCKS_MAX_UPLOAD_BYTES", 512 * 1024 * 1024))


@router.post("/workshops/{wid}/upload")
async def upload_audio(
    wid: str,
    file: UploadFile = File(...),  # noqa: B008
    request: Request = None,  # type: ignore[assignment]
) -> dict:
    """保存原音频到 ``cache/workshop_<wid>/raw_audio/``。

    行为：
    1. 分块写临时文件，再调用 ``MusicWorkshop.set_raw_audio(path, filename)``
    2. 自动命名（仅当 name 还是 ``"New Workshop"``）
    3. 写 state.json + emit ``raw_audio_set``

    返回 ``{"ok": true, "filename": <保存后的文件名>, "name": <新车间名>}``
    """
    kernel = _kernel(request)
    ws = kernel.manager.get(wid)
    if ws is None:
        _err(404, f"车间 {wid} 不存在")
    original_name = file.filename or "uploaded.mp3"
    dst_name = Path(original_name).name
    try:
        record, _ = kernel.tasks.admit(
            wid,
            "upload",
            (uuid4().hex,),
            track=None,
        )
    except TaskBusyError as error:
        _err(409, str(error))
    kernel.tasks.attach(record, asyncio.current_task())
    temp_path: Path | None = None
    try:
        kernel.tasks.stage(record, "receiving_upload")
        with tempfile.NamedTemporaryFile(
            dir=ws.cache.workshop_dir,
            suffix=Path(dst_name).suffix,
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            size = 0
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    _err(413, "上传文件超过大小限制")
                await _run_blocking(temp.write, chunk)
        if size == 0:
            _err(400, "上传文件为空")
        kernel.tasks.stage(record, "committing")
        abs_path = await _run_committing(
            ws.set_raw_audio,
            temp_path,
            f"{record.id}_{dst_name}",
            display_filename=dst_name,
            task_id=record.id,
        )
        if kernel.orchestrator is not None:
            kernel.orchestrator.get_context(wid).rc.clear()
        kernel.tasks.finish(record, "done")
    except asyncio.CancelledError:
        kernel.tasks.finish(record, "cancelled")
        raise
    except HTTPException as error:
        kernel.tasks.finish(record, "failed", str(error.detail))
        raise
    except (ValueError, TypeError, OSError) as e:
        kernel.tasks.finish(record, "failed", str(e))
        _err(400, f"保存失败: {e}")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return {
        "ok": True,
        "filename": abs_path.name,
        "name": ws.name,
        "rel_path": ws.state.tab_state.tab1.raw_audio_file_path,
    }


# ---------------------------------------------------------------------------
# URL 上传（**真实现**：调用 audio/loader.download_audio_from_url + 写 cache）
# ---------------------------------------------------------------------------


class UploadFromUrlRequest(BaseModel):
    url: str


@router.post("/workshops/{wid}/upload-by-url")
async def upload_from_url(
    wid: str,
    req: UploadFromUrlRequest,
    request: Request,
) -> dict:
    """从 URL（YouTube/Bilibili）下载音频并写入 ``cache/workshop_<wid>/raw_audio/``。

    流程：
    1. ``audio/loader.py::download_audio_from_url`` —— yt-dlp + ffmpeg 落到本地临时
    2. 通过文件路径导入车间缓存，避免整文件 bytes 副本

    Returns:
        ``{"ok": true, "filename": <落盘文件>, "name": <车间名>}``
    """
    kernel = _kernel(request)
    ws = kernel.manager.get(wid)
    if ws is None:
        _err(404, f"车间 {wid} 不存在")

    url = (req.url or "").strip()
    if not url:
        _err(400, "URL 不能为空")
    if not (url.startswith("http://") or url.startswith("https://")):
        _err(400, "URL 必须以 http(s):// 开头")

    from src.audio.loader import download_audio_from_url, get_video_title

    try:
        record, _ = kernel.tasks.admit(wid, "url_download", (uuid4().hex,))
    except TaskBusyError as error:
        _err(409, str(error))
    kernel.tasks.attach(record, asyncio.current_task())
    worker_result: dict[str, Path] = {}
    try:
        # 1. 先拿标题（用于自动命名车间）
        kernel.tasks.stage(record, "fetching_title")
        raw_title = await _run_blocking(get_video_title, url)

        # 2. 下载音频（带进度回调 → SSE 推给前端）
        bus = _bus(request)

        def progress_hook(d: dict) -> None:
            if d.get("status") == "downloading":
                pct_str = d.get("_percent_str", "0%").replace("%", "")
                try:
                    pct = float(pct_str) / 100.0
                except (ValueError, TypeError):
                    pct = 0.0
                bus.emit(
                    wid,
                    "url_download_progress",
                    {
                        "progress": pct,
                        "task_id": record.id,
                    },
                )

        def download():
            result = download_audio_from_url(url, format="mp3", progress_hook=progress_hook)
            worker_result["path"] = Path(result)
            return Path(result)

        kernel.tasks.stage(record, "downloading")
        tmp_path = await _run_blocking(download)
        bus.emit(
            wid,
            "url_download_progress",
            {
                "progress": 1.0,
                "task_id": record.id,
            },
        )

        # 3. 落 cache
        if tmp_path.stat().st_size > MAX_UPLOAD_BYTES:
            _err(413, "下载文件超过大小限制")
        safe_name = Path(url.split("?")[0].rstrip("/").split("/")[-1] or "yt_audio.mp3")
        if not safe_name.suffix:
            safe_name = safe_name.with_suffix(safe_name.suffix or ".mp3")
        kernel.tasks.stage(record, "committing")
        abs_path = await _run_committing(
            ws.set_raw_audio,
            tmp_path,
            f"{record.id}_{safe_name.name}",
            display_filename=safe_name.name,
            task_id=record.id,
        )
        if raw_title and ws.name == "New Workshop":
            from src.utils.naming import sanitize_title

            ws.rename(sanitize_title(raw_title))
        if kernel.orchestrator is not None:
            kernel.orchestrator.get_context(wid).rc.clear()
        kernel.tasks.finish(record, "done")
    except asyncio.CancelledError:
        kernel.tasks.finish(record, "cancelled")
        raise
    except HTTPException as error:
        kernel.tasks.finish(record, "failed", str(error.detail))
        raise
    except Exception as e:  # noqa: BLE001
        kernel.tasks.finish(record, "failed", str(e))
        msg = str(e)
        if "412" in msg or "bilibili" in msg.lower():
            msg += "（B站近期风控升级，yt-dlp 暂未适配）"
        _err(500, f"URL 下载失败: {msg}")
    finally:
        downloaded = worker_result.get("path")
        if downloaded is not None:
            downloaded.unlink(missing_ok=True)

    return {
        "ok": True,
        "filename": abs_path.name,
        "name": ws.name,
        "rel_path": ws.state.tab_state.tab1.raw_audio_file_path,
    }
