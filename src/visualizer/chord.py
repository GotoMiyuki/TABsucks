"""和弦标签可视化数据生成模块。

设计原则：
- 后端只返回物理量（时间、时长、和弦名称）
- 前端负责计算 pixels_per_second = canvas_width / duration
- 前端负责计算 x = chord.start * pps，width = chord.duration * pps
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChordLabelData:
    """和弦标签数据。

    只包含物理量，前端自行映射到像素坐标。
    不包含 pixels_per_second，避免前后端耦合。

    Attributes:
        chords: ChordEvent 列表，来自 src.analysis.chord
        duration: 音频总时长（秒），前端用于计算比例
    """

    chords: list
    duration: float

    def to_dict(self) -> list[dict]:
        """序列化为 JSON 数组。

        前端渲染示例（React/Vue Canvas）：
            const pps = canvasWidth / data.duration;
            data.chords.forEach(chord => {
                const x = chord.start * pps;
                const width = chord.duration * pps;
                ctx.fillStyle = '#eee';
                ctx.fillRect(x, 0, width, 30);
                ctx.fillStyle = '#000';
                ctx.fillText(chord.name, x + 4, 20);
            });
        """
        return [
            {
                "start": float(c.start),
                "end": float(c.end),
                "duration": float(c.duration),
                "name": c.name,
                "root": c.root,
                "quality": c.quality,
                "startProportion": float(c.start / max(self.duration, 0.001)),
                "durationProportion": float(c.duration / max(self.duration, 0.001)),
                "romanNumeral": c.roman_numeral,
            }
            for c in self.chords
        ]


def build_chord_labels(
    chords: list,
    duration: float | None = None,
) -> ChordLabelData:
    """从 ChordEvent 列表构建和弦标签数据。

    Args:
        chords: src.analysis.chord.ChordEvent 列表
        duration: 音频总时长（秒），如果为 None 则从最后一个和弦的 end 推断

    Returns:
        ChordLabelData 对象

    注意：
        不接收 canvas_width 参数！pixels_per_second 由前端计算。
    """
    if duration is None:
        if chords:
            duration = float(max(c.end for c in chords))
        else:
            duration = 0.0

    return ChordLabelData(chords=chords, duration=float(duration))