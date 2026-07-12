"""分离 / 分析 / 上传路由。

当前状态（MVP mock 模式）:

* 上传（``/upload``）—— 真实现：调 ``MusicWorkshop.set_raw_audio_from_bytes``
* 分离（``/separate``） —— **MOCK**：asyncio sleep 模拟进度，替换点 ``[MOCK] A``
* 分析（``/analyze``）  —— **MOCK**：asyncio sleep 模拟进度，替换点 ``[MOCK] B``
* 可视化（``/visualization``） —— **MOCK**：从本地 ``mock_data/demo.json`` 读，替换点 ``[MOCK] C``
* 音频（``/audio/<track>``） —— **MOCK**：造 5 秒合成 wav，替换点 ``[MOCK] D``

这些 ``[MOCK] X`` 标记后续插件层接入时按字母序查找。
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import random
import struct
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
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
# 分离（**MOCK**，替换点 A）
# ---------------------------------------------------------------------------


@router.post("/workshops/{wid}/separate")
def trigger_separation(
    wid: str,
    req: SeparateRequest,
    background: BackgroundTasks,
    request: Request,
) -> dict:
    """[MOCK] 替换点 A：接入真正 ``SeparatorPlugin.run()``。"""
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    bus = _bus(request)
    background.add_task(_run_mock_separation, wid, req.model, bus)
    return {"ok": True, "message": "分离已启动"}


async def _run_mock_separation(wid: str, model: str, bus) -> None:
    """[MOCK] 用 asyncio sleep 模拟分离进度。"""
    bus.emit(wid, "separation_started", {"model": model})
    for i in range(101):
        await asyncio.sleep(0.03)
        if i % 10 == 0:
            bus.emit(
                wid,
                "separation_progress",
                {"progress": i / 100.0},
            )
    bus.emit(
        wid,
        "separation_done",
        {
            "stems": [
                "vocals",
                "drums",
                "bass",
                "piano",
                "guitar",
                "other",
            ],
        },
    )


# ---------------------------------------------------------------------------
# 分析（**MOCK**，替换点 B）
# ---------------------------------------------------------------------------


@router.post("/workshops/{wid}/analyze")
def trigger_analysis(
    wid: str,
    req: AnalyzeRequest,
    background: BackgroundTasks,
    request: Request,
) -> dict:
    """[MOCK] 替换点 B：接入真正 ``AnalysisPlugin.run()``。"""
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    bus = _bus(request)
    background.add_task(_run_mock_analysis, wid, req.track, req.plugin, bus)
    return {"ok": True, "message": f"分析 {req.track} 已启动"}


async def _run_mock_analysis(
    wid: str, track: str, plugin: str, bus
) -> None:
    """[MOCK] mock 一次分析，约 1.5s 完成。"""
    bus.emit(wid, "analysis_started", {"track": track, "plugin": plugin})
    await asyncio.sleep(1.5)
    bus.emit(
        wid,
        "analysis_done",
        {
            "track": track,
            "plugin": plugin,
            "result": _mock_analysis_result(track),
        },
    )


# ---------------------------------------------------------------------------
# 可视化（**MOCK**，替换点 C）
# ---------------------------------------------------------------------------


@router.get("/workshops/{wid}/visualization")
def get_visualization(
    wid: str, track: str = "full", request: Request = None  # type: ignore[assignment]
) -> dict:
    """[MOCK] 替换点 C：接入 ``export_visualization_json()`` 真实数据。"""
    kernel = _kernel(request)
    if kernel.manager.get(wid) is None:
        _err(404, f"车间 {wid} 不存在")
    if track == "full":
        demo = MOCK_DATA_DIR / "demo.json"
        if demo.exists():
            with demo.open(encoding="utf-8") as f:
                return json.load(f)
    return {
        "waveform": _mock_waveform(track),
        "beats": _mock_beats(),
        "chords": _mock_chords(),
        "metadata": {
            "duration": 30.0,
            "sampleRate": 44100,
            "hasBeatData": True,
            "hasChordData": True,
        },
    }


# ---------------------------------------------------------------------------
# 音频流（**MOCK**，替换点 D）
# ---------------------------------------------------------------------------


@router.get("/workshops/{wid}/audio/{track}")
def get_audio(wid: str, track: str, request: Request = None  # type: ignore[assignment]
) -> StreamingResponse:
    """[MOCK] 替换点 D：返回 ``cache/workshop_<wid>/track_audio/track_<name>/<file>`` 的真实 wav。"""
    _ = wid  # 防止 unused warning
    wav = _generate_test_wav(track)
    return StreamingResponse(
        io.BytesIO(wav),
        media_type="audio/wav",
        headers={"Content-Disposition": f'inline; filename="{track}.wav"'},
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


def _mock_analysis_result(track: str) -> dict:
    return {
        "status": "success",
        "track": track,
        "plugin": "chord_ismir2019",
        "chords": _mock_chords(),
        "bpm": 120.0,
        "timeSignature": "4/4",
        "key": "C",
        "mode": "major",
        "confidence": 0.92,
    }


def _generate_test_wav(track: str = "other", duration: float = 5.0) -> bytes:
    import numpy as np

    sr = 44100
    freq = {
        "vocals": 440,
        "drums": 100,
        "bass": 80,
        "piano": 523,
        "guitar": 330,
    }.get(track, 660)
    n = int(sr * duration)
    samples = (
        0.5
        * np.sin(
            2 * np.pi * freq * np.linspace(0, duration, n)
        )
    ).astype(np.float32)
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


__all__ = ["router"]
