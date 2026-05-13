"""MIDI 导出模块，将分离音轨导出为标准 MIDI 文件。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.audio.loader import AudioData
    from src.separation.separator import SeparationResult


class MidiExporterError(Exception):
    """MIDI 导出失败时抛出。"""

    pass


class MidiExporter:
    """MIDI 文件导出器。"""

    def export(
        self,
        separation_result: SeparationResult,
        output_path: str | Path,
        start: float = 0.0,
        duration: float | None = None,
    ) -> None:
        """将分离结果导出为 MIDI 文件。

        Args:
            separation_result: 音轨分离结果。
            output_path: 输出 MIDI 文件路径。
            start: 起始时间（秒）。
            duration: 持续时长（秒），None 表示导出全部。

        Raises:
            MidiExporterError: 导出失败。
        """
        # TODO: 接入 MIDI 转换算法（如 SPIN、Melodia）
        # 目前仅创建空的 MIDI 占位文件
        output_path = Path(output_path)

        try:
            # 占位：创建空 MIDI 文件头
            header = "MIDI file placeholder\n"
            header += f"Source SR: {separation_result.sample_rate}\n"
            header += f"Export start: {start}s\n"
            header += f"Export duration: {duration}s\n"

            with output_path.open("w", encoding="utf-8") as f:
                f.write(header)
        except Exception as e:
            raise MidiExporterError(f"MIDI 导出失败: {e}") from e


def export_to_midi(
    separation_result: SeparationResult,
    output_path: str | Path,
    start: float = 0.0,
    duration: float | None = None,
) -> None:
    """便捷函数：将分离结果导出为 MIDI 文件。"""
    exporter = MidiExporter()
    exporter.export(separation_result, output_path, start, duration)
