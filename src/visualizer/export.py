"""统一导出可视化数据为 JSON，供前端消费。

接口设计原则：
- 一次性返回所有数据（波形必选，节拍/和弦可选）
- 后端只返回物理量，前端自行计算 pixels_per_second
- 可选择性导出（不强制包含节拍/和弦）
- 同时支持保存到文件（用于测试/CLI 演示）
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.audio.loader import AudioData
    from src.analysis.beat import BeatInfo
    from src.analysis.chord import ChordEvent


def export_visualization_json(
    audio: "AudioData",
    beat_info: "BeatInfo | None" = None,
    chord_events: list["ChordEvent"] | None = None,
    num_waveform_frames: int = 2000,
    output_path: str | Path | None = None,
) -> dict:
    """生成完整的可视化数据 JSON。

    后端只提供物理量。前端渲染时自行计算 pixels_per_second：
        const pps = canvasWidth / data.metadata.duration;

    Args:
        audio: AudioData 对象
        beat_info: BeatInfo 对象（可选，传 None 则不含节拍数据）
        chord_events: ChordEvent 列表（可选，传 None 则不含和弦数据）
        num_waveform_frames: 波形帧数，默认 2000
        output_path: 如果指定，则同时写入文件（可选）

    Returns:
        JSON-serializable 字典，结构如下：
        {
            "waveform": {
                "peaks": [0.12, 0.34, ...],   # 归一化峰值
                "duration": 245.5,             # 总时长（秒）
                "sampleRate": 44100,            # 采样率
                "frameInterval": 0.12275,        # 每帧秒数
                "totalFrames": 2000,
            },
            "beats": [                          # 或 null（未分析节拍时）
                {"time": 0.5, "measure": 1, "beatInMeasure": 1, "isDownbeat": true, "timeProportion": 0.002},
                ...
            ],
            "chords": [                        # 或 null（未分析和弦时）
                {"start": 0.0, "end": 2.5, "duration": 2.5, "name": "C:maj", "startProportion": 0.0, "durationProportion": 0.01},
                ...
            ],
            "metadata": {
                "duration": 245.5,
                "sampleRate": 44100,
                "hasBeatData": true,
                "hasChordData": true,
                "exportedAt": "2026-06-01T12:00:00",
            }
        }

    前端使用示例（React）：
        const res = await fetch(`/api/workspaces/${id}/visualization`);
        const data = await res.json();
        const pps = canvasWidth / data.metadata.duration;

        // 画波形
        data.waveform.peaks.forEach((peak, i) => {
            const x = i / data.waveform.totalFrames * canvasWidth;
            const h = peak * canvasHeight;
            ctx.fillRect(x, canvasHeight - h, barW, h);
        });

        // 画节拍线
        data.beats?.forEach(beat => {
            const x = beat.time * pps;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, canvasHeight);
            ctx.strokeStyle = beat.isDownbeat ? 'red' : 'gray';
            ctx.stroke();
        });

        // 画和弦标签
        data.chords?.forEach(chord => {
            const x = chord.start * pps;
            const w = chord.duration * pps;
            ctx.fillRect(x, 0, w, 30);
            ctx.fillText(chord.name, x + 4, 20);
        });
    """
    from src.visualizer.waveform import compute_waveform
    from src.visualizer.beat import build_beat_markers
    from src.visualizer.chord import build_chord_labels

    duration = float(audio.duration)

    # 波形（必选）
    waveform = compute_waveform(audio, num_frames=num_waveform_frames)

    # 节拍（可选）
    beat_data = None
    if beat_info is not None:
        beat_data = build_beat_markers(beat_info, duration=duration)

    # 和弦（可选）
    chord_data = None
    if chord_events:
        chord_data = build_chord_labels(chord_events, duration=duration)

    result = {
        "waveform": waveform.to_dict(),
        "beats": beat_data.to_dict() if beat_data else None,
        "chords": chord_data.to_dict() if chord_data else None,
        "metadata": {
            "duration": duration,
            "sampleRate": int(audio.sample_rate),
            "hasBeatData": beat_data is not None,
            "hasChordData": chord_data is not None,
            "exportedAt": datetime.now().isoformat(),
        },
    }

    if output_path is not None:
        path = Path(output_path)
        with path.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    return result