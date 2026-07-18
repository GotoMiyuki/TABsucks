"""上传、分离、分析和 Tab4 可视化路由。

分离与分析任务通过 Kernel/Orchestrator 启动。可视化读取真实原音频或分离 stem，
并合并对应音轨最新完成的分析结果；音频端点直接返回车间中的真实文件。
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

MOCK_DATA_DIR = (
    Path(__file__).parent.parent / "mock_data"
)

router = APIRouter()


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class SeparateRequest(BaseModel):
    model: str = "BS-RoFormer-SW"
    device: Literal["cpu", "gpu"] = "gpu"


class AnalyzeRequest(BaseModel):
    track: str
    plugin: str = "chord_ismir2019"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 上传音频（**真实现**：写入 cache/ + 自动命名）
# ---------------------------------------------------------------------------


@router.post("/workshops/{wid}/upload")
async def upload_audio(
    wid: str,
    file: UploadFile = File(...),  # noqa: B008
    request: Request = None,  # type: ignore[assignment]
) -> dict:
    """保存原音频到 ``cache/workshop_<wid>/raw_audio/``。

    行为：
    1. 调 ``MusicWorkshop.set_raw_audio_from_bytes(data, filename)``
    2. 自动命名（仅当 name 还是 ``"New Workshop"``）
    3. 写 state.json + emit ``raw_audio_set``

    返回 ``{"ok": true, "filename": <保存后的文件名>, "name": <新车间名>}``
    """
    kernel = _kernel(request)
    ws = kernel.manager.get(wid)
    if ws is None:
        _err(404, f"车间 {wid} 不存在")
    content = await file.read()
    if not content:
        _err(400, "上传文件为空")
    original_name = file.filename or "uploaded.mp3"
    # 走 cache_path 安全处理
    dst_name = Path(original_name).name
    try:
        abs_path = ws.set_raw_audio_from_bytes(content, dst_name)
    except (ValueError, TypeError, OSError) as e:
        _err(400, f"保存失败: {e}")
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
    2. 读 ``Path.read_bytes()`` 成 bytes
    3. 调 :py:meth:`MusicWorkshop.set_raw_audio_from_bytes` 写 cache + 自动命名

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
        # 1. 先拿标题（用于自动命名车间）
        raw_title = get_video_title(url)
        if raw_title and ws.name == "New Workshop":
            from src.utils.naming import sanitize_title
            ws.rename(sanitize_title(raw_title))

        # 2. 下载音频（带进度回调 → SSE 推给前端）
        bus = _bus(request)

        def progress_hook(d: dict) -> None:
            if d.get("status") == "downloading":
                pct_str = d.get("_percent_str", "0%").replace("%", "")
                try:
                    pct = float(pct_str) / 100.0
                except (ValueError, TypeError):
                    pct = 0.0
                bus.emit(wid, "url_download_progress", {"progress": pct})

        tmp_path = download_audio_from_url(
            url, format="mp3", progress_hook=progress_hook
        )  # type: ignore[arg-type]
        bus.emit(wid, "url_download_progress", {"progress": 1.0})

        # 3. 落 cache
        content = tmp_path.read_bytes()
        safe_name = Path(url.split("?")[0].rstrip("/").split("/")[-1] or "yt_audio.mp3")
        if not safe_name.suffix:
            safe_name = safe_name.with_suffix(safe_name.suffix or ".mp3")
        abs_path = ws.set_raw_audio_from_bytes(content, safe_name.name)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "412" in msg or "bilibili" in msg.lower():
            msg += "（B站近期风控升级，yt-dlp 暂未适配）"
        _err(500, f"URL 下载失败: {msg}")

    return {
        "ok": True,
        "filename": abs_path.name,
        "name": ws.name,
        "rel_path": ws.state.tab_state.tab1.raw_audio_file_path,
    }


# ---------------------------------------------------------------------------
# 分离（**MOCK**，替换点 A）
# ---------------------------------------------------------------------------


@router.post("/workshops/{wid}/separate")
async def trigger_separation(
    wid: str,
    req: SeparateRequest,
    request: Request,
) -> dict:
    """**真实现**：调 :py:meth:`Kernel.start_separation_task` 启动。

    进度经 EventBus 推到 SSE，前端订阅 ``separation_progress`` / ``separation_done`` /
    ``separation_failed``。返回 ``{"ok": true, "task": "<plugin>"}`` 即可，
    不阻塞 request。
    """
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    # 由 Orchestrator.start_separation 异步 emit "separation_started/progress/done"
    try:
        kernel.start_separation_task(
            wid,
            plugin_name=req.model,
            compute_device=req.device,
            durations_sec=3.0,
        )
    except Exception as e:  # noqa: BLE001
        _err(400, f"启动分离失败: {e}")
    # 不 await — 让 FastAPI BackgroundTasks 的协程跑完
    return {"ok": True, "task": req.model}


# ---------------------------------------------------------------------------
# 分析（**真实现**，替换点 B —— 接 Kernel.start_analysis_task）
# ---------------------------------------------------------------------------


@router.post("/workshops/{wid}/analyze")
async def trigger_analysis(
    wid: str,
    req: AnalyzeRequest,
    request: Request,
) -> dict:
    """**真实现**：调 :py:meth:`Kernel.start_analysis_task`。

    事件经 EventBus → SSE：``analysis_started / progress / done / failed``。
    """
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    kernel.start_analysis_task(
        wid,
        plugin_name=req.plugin,
        stem_name=req.track,
        durations_sec=1.5,
    )
    return {"ok": True, "task": req.plugin}


@router.get("/workshops/{wid}/analysis-results")
def get_analysis_results(wid: str, request: Request) -> dict:
    """Return the latest persisted analysis result for each track."""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid)
    if ws is None:
        _err(404, f"Workshop {wid} not found")

    results: dict[str, dict] = {}
    result_plugins: dict[str, str] = {}
    for key, task_state in ws.state.tab_state.tab3.items():
        if (
            task_state.analysis_state != "done"
            or task_state.analysis_result_path is None
        ):
            continue

        track_name = key.split("::", 1)[0]
        try:
            result_path = ws.cache.to_absolute(
                task_state.analysis_result_path
            )
            result_data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue

        if isinstance(result_data, list):
            result_data = {"chords": result_data}
        if isinstance(result_data, dict):
            results[track_name] = result_data
            if task_state.analysis_tool_name:
                result_plugins[track_name] = task_state.analysis_tool_name

    return {
        "ok": True,
        "results": results,
        "result_plugins": result_plugins,
    }


# ---------------------------------------------------------------------------
# 可视化（**MOCK**，替换点 C）
# ---------------------------------------------------------------------------


@router.get("/workshops/{wid}/visualization")
def get_visualization(
    wid: str, track: str = "full", request: Request = None  # type: ignore[assignment]
) -> dict:
    """返回可视化 JSON（波形 + 节拍 + 和弦），优先读真实分析结果。"""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid) if kernel.manager else None
    if ws is None:
        _err(404, f"车间 {wid} 不存在")

    # 1. 波形：full 读 raw audio，具体 track 读对应 stem
    waveform_data = _build_waveform(ws, track)

    # 2. 节拍 / 和弦：从 Tab3 分析结果读取
    beat_data = None
    chord_data = None
    duration = waveform_data.get("duration", 30.0)

    tab3 = ws.state.tab_state.tab3
    if tab3:
        beat_data, chord_data = _extract_visualization_from_tab3(ws, track, duration)

    return {
        "waveform": waveform_data,
        "beats": beat_data or _mock_beats(duration),
        # Tab4 不能把生成的占位和弦当作模型分析结果。
        "chords": chord_data or [],
        "metadata": {
            "duration": duration,
            "sampleRate": waveform_data.get("sampleRate", 44100),
            "hasBeatData": beat_data is not None,
            "hasChordData": chord_data is not None,
        },
    }


def _build_waveform(ws, track: str = "full") -> dict:
    """从 raw audio 或指定 stem 计算波形峰值数据。"""
    try:
        if track == "full":
            audio_path = ws.get_raw_audio_path()
        else:
            audio_path = ws.get_track_audio_paths().get(track)
        if audio_path is None or not audio_path.is_file():
            raise FileNotFoundError
        from src.audio.loader import load_audio_multi_channel

        audio = load_audio_multi_channel(audio_path)
        from src.visualizer.waveform import compute_waveform

        wf = compute_waveform(audio, num_frames=2000)
        return wf.to_dict()
    except Exception:  # noqa: BLE001
        return _mock_waveform(track)


def _extract_visualization_from_tab3(ws, track: str, duration: float) -> tuple:
    """从 Tab3 分析结果中提取节拍和和弦数据。

    Returns:
        ``(beat_data | None, chord_data | None)``
    """
    beat_data = None
    chord_data = None

    for key, task_state in reversed(
        list(ws.state.tab_state.tab3.items())
    ):
        if task_state.analysis_state != "done":
            continue
        if task_state.analysis_result_path is None:
            continue

        track_name, _ = key.split("::", 1) if "::" in key else (key, "")
        if track != "full" and track_name != track:
            continue

        try:
            abs_path = ws.cache.to_absolute(task_state.analysis_result_path)
        except ValueError:
            continue

        if not abs_path.is_file():
            continue

        try:
            import json as _json

            raw = _json.loads(abs_path.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            continue

        tool = task_state.analysis_tool_name or ""

        # 节奏分析结果
        if "rhythm" in tool and beat_data is None:
            beat_data = _build_beats_from_result(raw, duration)

        # 和弦分析结果
        if ("chord" in tool or "chord" in key) and chord_data is None:
            chord_data = _build_chords_from_result(raw, duration)

        if beat_data is not None and chord_data is not None:
            break

    return beat_data, chord_data


def _build_beats_from_result(raw: dict, duration: float) -> list[dict] | None:
    """从分析结果中提取节拍数据。"""
    bpm = raw.get("bpm") or raw.get("global_bpm")
    if not bpm:
        return None
    interval = 60.0 / float(bpm)
    beats, t, n = [], 0.0, 1
    while t < duration:
        beats.append({
            "time": round(t, 4),
            "measure": (n - 1) // 4 + 1,
            "beatInMeasure": (n - 1) % 4 + 1,
            "isDownbeat": (n - 1) % 4 == 0,
            "timeProportion": round(t / duration, 6),
        })
        t += interval
        n += 1
    return beats


def _build_chords_from_result(raw: dict, duration: float) -> list[dict] | None:
    """从分析结果中提取和弦数据。"""
    chords = raw.get("chords") or raw.get("chord_labels")
    if not isinstance(chords, list) or not chords:
        return None
    result = []
    for c in chords:
        if not isinstance(c, dict):
            continue
        start = float(c.get("start", 0))
        end = float(c.get("end", 0))
        result.append({
            "start": round(start, 4),
            "end": round(end, 4),
            "duration": round(end - start, 4),
            "name": c.get("name", c.get("chord", "?")),
            "root": c.get("root", ""),
            "quality": c.get("quality", ""),
            "startProportion": round(start / duration, 6) if duration > 0 else 0,
            "durationProportion": round((end - start) / duration, 6) if duration > 0 else 0,
        })
    return result or None


# ---------------------------------------------------------------------------
# 音频流（**MOCK**，替换点 D）
# ---------------------------------------------------------------------------


@router.get("/workshops/{wid}/audio/{track}")
def get_audio(wid: str, track: str, request: Request = None  # type: ignore[assignment]
) -> FileResponse:
    """返回 ``cache/workshop_<wid>/track_audio/track_<name>/<file>`` 的真实 wav。"""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid) if kernel.manager else None
    if ws is None:
        _err(404, f"车间 {wid} 不存在")

    track_paths = ws.get_track_audio_paths()
    abs_path = track_paths.get(track)
    if abs_path is None or not abs_path.is_file():
        # Fallback: serve raw audio if separation not done
        raw = ws.get_raw_audio_path()
        if raw and raw.is_file():
            abs_path = raw
        else:
            _err(404, f"音轨 {track} 不存在")

    return FileResponse(
        abs_path,
        media_type="audio/wav",
        filename=f"{track}.wav",
        content_disposition_type="inline",
    )


@router.get("/workshops/{wid}/midi")
def export_selected_tracks_midi(
    wid: str,
    tracks: list[str] | None = Query(default=None),
    request: Request = None,  # type: ignore[assignment]
) -> Response:
    """将当前 Tab2 已选音轨的最新和弦结果导出为多轨 MIDI。"""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid) if kernel.manager else None
    if ws is None:
        _err(404, f"车间 {wid} 不存在")

    selected = ws.get_selected_tracks()
    requested_set = set(tracks or selected)
    invalid = sorted(requested_set.difference(selected))
    if invalid:
        _err(409, f"包含当前未选择的音轨: {invalid}")
    requested = [track for track in selected if track in requested_set]
    if not requested:
        _err(409, "Tab2 尚未选择可导出的音轨")

    track_chords = {}
    missing = []
    for track in requested:
        _, chords = _extract_visualization_from_tab3(ws, track, duration=1.0)
        if chords:
            track_chords[track] = chords
        else:
            missing.append(track)
    if missing:
        _err(409, f"以下音轨没有可导出的和弦结果: {missing}")

    from src.kernel.core.midi_exporter import (
        MidiExporterError,
        export_chord_tracks_to_midi,
    )

    try:
        midi_data = export_chord_tracks_to_midi(track_chords)
    except MidiExporterError as e:
        _err(409, str(e))

    filename = f"tabsucks_{wid}_selected.mid"
    return Response(
        content=midi_data,
        media_type="audio/midi",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Mock 数据生成
# ---------------------------------------------------------------------------


def _peaks(n: int = 2000, seed: int = 42) -> list[float]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        t = i / n
        base = 0.3 + 0.2 * math.sin(2 * math.pi * t * 3)
        detail = 0.15 * math.sin(2 * math.pi * t * 17 + rng.random())
        noise = rng.gauss(0, 0.05)
        out.append(max(0.02, min(1.0, base + detail + noise)))
    return out


def _mock_waveform(track: str = "full") -> dict:
    seed = {
        "vocals": 1,
        "drums": 2,
        "bass": 3,
        "piano": 4,
        "guitar": 5,
        "other": 6,
    }.get(track, 0)
    return {
        "peaks": _peaks(2000, seed),
        "duration": 30.0,
        "sampleRate": 44100,
        "frameInterval": 0.015,
        "totalFrames": 2000,
    }


def _mock_beats(duration: float = 30.0, bpm: float = 120.0) -> list[dict]:
    interval = 60.0 / bpm
    beats, t, n = [], 0.0, 1
    while t < duration:
        beats.append(
            {
                "time": round(t, 4),
                "measure": (n - 1) // 4 + 1,
                "beatInMeasure": (n - 1) % 4 + 1,
                "isDownbeat": (n - 1) % 4 == 0,
                "timeProportion": round(t / duration, 6),
            }
        )
        t += interval
        n += 1
    return beats


def _mock_chords(duration: float = 30.0) -> list[dict]:
    prog = [
        ("C", "maj"),
        ("C", "maj"),
        ("A", "m"),
        ("A", "m"),
        ("F", "maj"),
        ("F", "maj"),
        ("G", "maj"),
        ("G", "maj"),
    ]
    dur = duration / len(prog)
    return [
        {
            "start": round(i * dur, 4),
            "end": round((i + 1) * dur, 4),
            "duration": round(dur, 4),
            "name": f"{r}:{q}",
            "root": r,
            "quality": q,
            "startProportion": round(i * dur / duration, 6),
            "durationProportion": round(dur / duration, 6),
            "romanNumeral": f"??{r}{q}",
        }
        for i, (r, q) in enumerate(prog)
    ]


__all__ = ["router"]
