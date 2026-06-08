"""节拍标记可视化数据生成模块。

设计原则：
- 后端只返回物理量（时间、拍号）
- 前端负责计算 pixels_per_second = canvas_width / duration
- 前端负责计算 x 坐标 = beat.time * pixels_per_second
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeatMarkerData:
    """节拍标记数据。

    只包含物理量，前端自行映射到像素坐标。
    不包含 pixels_per_second，避免前后端耦合。

    Attributes:
        beats: BeatEvent 列表，来自 src.analysis.beat
        duration: 音频总时长（秒），前端用于计算比例
    """

    beats: list
    duration: float

    def to_dict(self) -> list[dict]:
        """序列化为 JSON 数组。

        前端渲染示例（React/Vue Canvas）：
            const pps = canvasWidth / data.duration;
            data.beats.forEach(beat => {
                const x = beat.time * pps;
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, canvasHeight);
                ctx.strokeStyle = beat.isDownbeat ? '#ff0000' : '#888';
                ctx.stroke();
            });
        """
        return [
            {
                "time": float(beat.time),
                "measure": beat.measure,
                "beatInMeasure": beat.beat_in_measure,
                "isDownbeat": beat.beat_in_measure == 1,
                "timeProportion": float(beat.time / max(self.duration, 0.001)),
            }
            for beat in self.beats
        ]


def build_beat_markers(
    beat_info,
    duration: float | None = None,
) -> BeatMarkerData:
    """从 BeatInfo 构建节拍标记数据。

    Args:
        beat_info: src.analysis.beat.BeatInfo 对象
        duration: 音频总时长（秒），如果为 None 则从 beat_info 推断

    Returns:
        BeatMarkerData 对象

    注意：
        不接收 canvas_width 参数！pixels_per_second 由前端计算。
        这样修改前端 Canvas 宽度无需重新请求后端。
    """
    beats = beat_info.beat_events if beat_info.beat_events else []

    if duration is None:
        if beat_info.bpm and beat_info.bpm > 0 and len(beats) > 0:
            # 从 BPM 和节拍数估算总时长
            beats_per_measure = beat_info.beats_per_measure
            measures = beats[-1].beat_number / beats_per_measure
            duration = measures * 60.0 / beat_info.bpm
        else:
            duration = 0.0

    return BeatMarkerData(beats=beats, duration=float(duration))