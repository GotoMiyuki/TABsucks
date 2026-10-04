"""Read-only visualization, audio streaming and real chord MIDI export."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import mimetypes
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response

from .dependencies import _err, _kernel

logger = logging.getLogger(__name__)

router = APIRouter()

TRACKS = {"full", "vocals", "drums", "bass", "piano", "guitar", "other"}


def _audio_path(ws, track):
    if track not in TRACKS:
        _err(400, "无效音轨名称")
    return ws.get_raw_audio_path() if track == "full" else ws.get_track_audio_paths().get(track)


def _source_version(ws, track):
    path = _audio_path(ws, track)
    if path is None or not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return hashlib.sha256(f"{ws.id}:{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[
        :24
    ]


def _result_reference(ws, key, task):
    path = ws.cache.to_absolute(task.analysis_result_path)
    stat = path.stat()
    track = key.split("::", 1)[0]
    value = (
        f"{ws.id}:{key}:{task.analysis_result_path}:{stat.st_mtime_ns}:{_source_version(ws, track)}"
    )
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _valid_chord_results(ws, track, duration):
    for key, task in reversed(list(ws.state.tab_state.tab3.items())):
        if (
            key.split("::", 1)[0] != track
            or task.analysis_state != "done"
            or not task.analysis_result_path
        ):
            continue
        if "chord" not in (task.analysis_tool_name or "") and "chord" not in key:
            continue
        try:
            ref = _result_reference(ws, key, task)
            raw = json.loads(
                ws.cache.to_absolute(task.analysis_result_path).read_text(encoding="utf-8")
            )
            if isinstance(raw, list):
                raw = {"chords": raw}
            chords = _build_chords_from_result(raw, duration) if isinstance(raw, dict) else None
            if chords:
                yield chords, {
                    "id": ref,
                    "plugin": task.analysis_tool_name,
                    "taskId": task.analysis_task_id,
                }
        except (OSError, ValueError, TypeError):
            continue


def _chord_result(ws, track, duration, result_id=None):
    for chords, descriptor in _valid_chord_results(ws, track, duration):
        if result_id is None or descriptor["id"] == result_id:
            return chords, descriptor
    if result_id is not None:
        _err(409, "分析结果已失效或不属于当前音轨，请刷新后重试")
    return None, None


def _available_chord_results(ws, track, duration):
    """Describe valid historical results without exposing cache file paths."""
    return [descriptor for _, descriptor in _valid_chord_results(ws, track, duration)]


@router.get("/workshops/{wid}/visualization")
def get_visualization(
    wid: str,
    track: str = "full",
    result_id: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict:
    """返回可视化 JSON（波形 + 节拍 + 和弦），优先读真实分析结果。"""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid) if kernel.manager else None
    if ws is None:
        _err(404, f"车间 {wid} 不存在")

    # 1. 波形：full 读 raw audio，具体 track 读对应 stem
    _audio_path(ws, track)
    waveform_data = _build_waveform(ws, track)

    # 2. 节拍 / 和弦：从 Tab3 分析结果读取
    beat_data = None
    chord_data = None
    duration = waveform_data.get("duration", 30.0)

    tab3 = ws.state.tab_state.tab3
    if tab3:
        beat_data, chord_data = _extract_visualization_from_tab3(ws, track, duration)
    result = None
    if track != "full":
        chord_data, result = _chord_result(ws, track, duration, result_id)
    elif result_id:
        _err(400, "原曲不支持分轨分析结果标识")

    return {
        "waveform": waveform_data,
        "beats": beat_data or [],
        # Tab4 不能把生成的占位和弦当作模型分析结果。
        "chords": chord_data or [],
        "metadata": {
            "duration": duration,
            "sampleRate": waveform_data.get("sampleRate", 44100),
            "hasAudioData": bool(waveform_data.get("totalFrames")),
            "hasBeatData": beat_data is not None,
            "hasChordData": chord_data is not None,
            "sourceVersion": _source_version(ws, track),
            "result": result,
            "availableResults": (
                _available_chord_results(ws, track, duration) if track != "full" else []
            ),
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
        stat = audio_path.stat()
        return _waveform_for_file(str(audio_path), stat.st_size, stat.st_mtime_ns)
    except Exception:  # noqa: BLE001
        logger.warning("Cannot build waveform for %s/%s", ws.id, track, exc_info=True)
        return {
            "peaks": [],
            "duration": 0.0,
            "sampleRate": 0,
            "frameInterval": 0.0,
            "totalFrames": 0,
        }


@lru_cache(maxsize=24)
def _waveform_for_file(path: str, size: int, modified: int) -> dict:
    """Cache only compact peaks, never decoded audio; stat values invalidate overwrites."""
    from src.audio.loader import load_audio_multi_channel
    from src.visualizer.waveform import compute_waveform

    audio = load_audio_multi_channel(Path(path))
    return compute_waveform(audio, num_frames=2000).to_dict()


def _extract_visualization_from_tab3(ws, track: str, duration: float) -> tuple:
    """从 Tab3 分析结果中提取节拍和和弦数据。

    Returns:
        ``(beat_data | None, chord_data | None)``
    """
    beat_data = None
    chord_data = None

    for key, task_state in reversed(list(ws.state.tab_state.tab3.items())):
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

        if isinstance(raw, list):
            raw = {"chords": raw}
        if not isinstance(raw, dict):
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
    try:
        bpm = float(raw.get("bpm") or raw.get("global_bpm") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(bpm) or bpm <= 0 or duration <= 0:
        return None
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


def _build_chords_from_result(raw: dict, duration: float) -> list[dict] | None:
    """从分析结果中提取和弦数据。"""
    chords = raw.get("chords") or raw.get("chord_labels")
    if not isinstance(chords, list) or not chords:
        return None
    result = []
    for c in chords:
        if not isinstance(c, dict):
            continue
        try:
            start = float(c.get("start", 0))
            end = float(c.get("end", 0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        result.append(
            {
                "start": round(start, 4),
                "end": round(end, 4),
                "duration": round(end - start, 4),
                "name": c.get("name", c.get("chord", "?")),
                "root": c.get("root", ""),
                "quality": c.get("quality", ""),
                "startProportion": round(start / duration, 6) if duration > 0 else 0,
                "durationProportion": round((end - start) / duration, 6) if duration > 0 else 0,
            }
        )
    return result or None


# ---------------------------------------------------------------------------
# 音频文件响应
# ---------------------------------------------------------------------------


@router.get("/workshops/{wid}/audio/{track}")
@router.head("/workshops/{wid}/audio/{track}", include_in_schema=False)
def get_audio(
    wid: str,
    track: str,
    download: bool = False,
    v: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> FileResponse:
    """返回 ``cache/workshop_<wid>/track_audio/track_<name>/<file>`` 的真实 wav。"""
    kernel = _kernel(request)
    ws = kernel.manager.get(wid) if kernel.manager else None
    if ws is None:
        _err(404, f"车间 {wid} 不存在")

    abs_path = _audio_path(ws, track)
    if abs_path is None or not abs_path.is_file():
        _err(404, f"音轨 {track} 不存在")
    if v is not None and v != _source_version(ws, track):
        _err(409, "音轨已更新，请刷新后重试")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", ws.name).strip(" .")[:100] or "project"

    return FileResponse(
        abs_path,
        media_type=mimetypes.guess_type(abs_path.name)[0] or "application/octet-stream",
        filename=f"{name}_{track}{abs_path.suffix}",
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/workshops/{wid}/midi")
def export_selected_tracks_midi(
    wid: str,
    tracks: Annotated[list[str] | None, Query()] = None,
    result_ids: Annotated[list[str] | None, Query()] = None,
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
    active = kernel.tasks.active(wid)
    if active and (
        active.kind == "separation" or active.kind == "analysis" and active.track in requested
    ):
        _err(409, "相关音轨正在处理，请等待任务结束后导出")

    references = {}
    if result_ids is not None:
        if not tracks or len(result_ids) != len(tracks) or len(set(tracks)) != len(tracks):
            _err(400, "结果标识必须与音轨逐一对应")
        references = dict(zip(tracks, result_ids, strict=True))

    track_chords = {}
    missing = []
    for track in requested:
        if references and _source_version(ws, track) is None:
            _err(409, "音轨文件已失效，请重新分离")
        chords, _ = _chord_result(ws, track, duration=1.0, result_id=references.get(track))
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
