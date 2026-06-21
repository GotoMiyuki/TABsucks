"""分离与分析路由（Mock 模式）。替换清单见 docs/ui_mock_swap.md"""

from __future__ import annotations

import asyncio
import io
import json
import math
import random
import struct

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.kernel.core.event_bus import WorkshopEvent, bus
from src.ui.api.workshops import wm

router = APIRouter(tags=["analysis"])

MOCK_DATA_DIR = __file__.rsplit("/", 1)[0].rsplit("\\", 1)[0] + "/../mock_data"


class SeparateRequest(BaseModel):
    model: str = "BS-RoFormer"


class AnalyzeRequest(BaseModel):
    track: str
    plugin: str = "chord_chordnet_2e1d"


# ─────────────────── mock helpers ───────────────────


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
    seed = {"vocals": 1, "drums": 2, "bass": 3, "piano": 4, "guitar": 5, "other": 6}.get(track, 0)
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


def _mock_chords(duration: float = 30.0) -> list[dict]:
    prog = [("C", "maj"), ("C", "maj"), ("A", "m"), ("A", "m"),
            ("F", "maj"), ("F", "maj"), ("G", "maj"), ("G", "maj")]
    dur = duration / len(prog)
    return [{
        "start": round(i * dur, 4),
        "end": round((i + 1) * dur, 4),
        "duration": round(dur, 4),
        "name": f"{r}:{q}",
        "root": r,
        "quality": q,
        "startProportion": round(i * dur / duration, 6),
        "durationProportion": round(dur / duration, 6),
        "romanNumeral": f"??{r}{q}",
    } for i, (r, q) in enumerate(prog)]


def _mock_analysis_result(track: str) -> dict:
    return {
        "status": "success",
        "track": track,
        "plugin": "chord_chordnet_2e1d",
        "chords": _mock_chords(),
        "bpm": 120.0,
        "timeSignature": "4/4",
        "key": "C",
        "mode": "major",
        "confidence": 0.92,
    }


def _generate_test_wav(track: str = "other", duration: float = 5.0) -> bytes:
    sr = 44100
    freq = {"vocals": 440, "drums": 100, "bass": 80, "piano": 523, "guitar": 330}.get(track, 660)
    n = int(sr * duration)
    import numpy as np
    samples = (0.5 * np.sin(2 * np.pi * freq * np.linspace(0, duration, n))).astype(np.float32)
    buf = io.BytesIO()
    data_size = n * 4  # 32-bit mono
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 4, 4, 32))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(samples.tobytes())
    return buf.getvalue()


# ─────────────────── endpoints ───────────────────


@router.post("/workshops/{wid}/upload")
async def upload_audio(wid: str, file: UploadFile = File(...)):
    ws = wm._workspaces.get(wid)
    if not ws:
        raise HTTPException(404, "车间不存在")
    ws.audio_path = file.filename
    ws.name = file.filename or ws.name
    return {"ok": True, "filename": file.filename}


@router.post("/workshops/{wid}/separate")
async def trigger_separation(wid: str, req: SeparateRequest):
    if wid not in wm._workspaces:
        raise HTTPException(404, "车间不存在")
    asyncio.create_task(_run_mock_separation(wid))
    return {"ok": True, "message": "分离已启动"}


async def _run_mock_separation(wid: str):
    """[MOCK] 替换点 A：接入真正 Separator.separate()"""
    bus.emit(WorkshopEvent(wid, "separation_started", {"model": "BS-RoFormer"}))
    for i in range(101):
        await asyncio.sleep(0.03)
        bus.emit(WorkshopEvent(wid, "separation_progress", {"progress": i / 100}))
    bus.emit(WorkshopEvent(wid, "separation_done", {
        "stems": ["vocals", "drums", "bass", "piano", "guitar", "other"],
    }))


@router.post("/workshops/{wid}/analyze")
async def trigger_analysis(wid: str, req: AnalyzeRequest):
    if wid not in wm._workspaces:
        raise HTTPException(404, "车间不存在")
    asyncio.create_task(_run_mock_analysis(wid, req.track))
    return {"ok": True, "message": f"分析 {req.track} 已启动"}


async def _run_mock_analysis(wid: str, track: str):
    """[MOCK] 替换点 B：接入真正 AnalysisEngine.run_single()"""
    bus.emit(WorkshopEvent(wid, "analysis_started", {"track": track}))
    await asyncio.sleep(1.5)
    bus.emit(WorkshopEvent(wid, "analysis_done", {
        "track": track,
        "plugin": "chord_chordnet_2e1d",
        "result": _mock_analysis_result(track),
    }))


@router.get("/workshops/{wid}/visualization")
async def get_visualization(wid: str, track: str = "full"):
    """[MOCK] 替换点 C：接入 export_visualization_json()"""
    if wid not in wm._workspaces:
        raise HTTPException(404, "车间不存在")
    if track == "full":
        try:
            with open(f"{MOCK_DATA_DIR}/demo.json", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            pass
    return {
        "waveform": _mock_waveform(track),
        "beats": _mock_beats(),
        "chords": _mock_chords(),
        "metadata": {"duration": 30.0, "sampleRate": 44100, "hasBeatData": True, "hasChordData": True},
    }


@router.get("/workshops/{wid}/audio/{track}")
async def get_audio(wid: str, track: str):
    """[MOCK] 替换点 D：返回真实分离后的音轨 WAV"""
    wav = _generate_test_wav(track)
    return StreamingResponse(io.BytesIO(wav), media_type="audio/wav",
                             headers={"Content-Disposition": f'inline; filename="{track}.wav"'})
