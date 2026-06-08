"""可视化数据生成模块。

导出接口：
    compute_waveform()    — 生成波形峰值数据
    build_beat_markers() — 生成节拍标记数据
    build_chord_labels() — 生成和弦标签数据
    export_visualization_json() — 统一导出为 JSON（供前端消费）
"""

from __future__ import annotations

from src.visualizer.waveform import compute_waveform, WaveformData
from src.visualizer.beat import build_beat_markers, BeatMarkerData
from src.visualizer.chord import build_chord_labels, ChordLabelData
from src.visualizer.export import export_visualization_json

__all__ = [
    "compute_waveform",
    "WaveformData",
    "build_beat_markers",
    "BeatMarkerData",
    "build_chord_labels",
    "ChordLabelData",
    "export_visualization_json",
]