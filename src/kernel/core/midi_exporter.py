"""MIDI 导出模块，将分离音轨导出为标准 MIDI 文件。"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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


_TRACK_PROGRAMS = {
    "vocals": 52,
    "drums": 0,
    "bass": 33,
    "piano": 0,
    "guitar": 24,
    "other": 48,
}
_TRACK_ROOT_BASE = {
    "bass": 36,
    "guitar": 48,
}
_ROOT_TO_SEMITONE = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}


def _chord_pitches(chord: dict[str, Any], track: str) -> list[int]:
    label = str(chord.get("name") or chord.get("chord") or "").strip()
    if label.upper() in {"", "N", "X", "NO_CHORD"}:
        return []

    root = str(chord.get("root") or "").strip()
    quality = str(chord.get("quality") or "").strip()
    if not root:
        match = re.match(r"^([A-Ga-g])([#b]?)(?::?([^/]*))?", label)
        if match is None:
            return []
        root = f"{match.group(1).upper()}{match.group(2)}"
        quality = quality or (match.group(3) or "")

    match = re.match(r"^([A-Ga-g])([#b]?)$", root)
    if match is None:
        return []
    semitone = _ROOT_TO_SEMITONE[match.group(1).upper()]
    if match.group(2) == "#":
        semitone += 1
    elif match.group(2) == "b":
        semitone -= 1
    semitone %= 12

    normalized = quality.lower().replace(":", "")
    is_minor = (
        normalized.startswith("min")
        or (normalized.startswith("m") and not normalized.startswith("maj"))
    )
    if "dim" in normalized:
        intervals = [0, 3, 6]
    elif "aug" in normalized or normalized.startswith("+"):
        intervals = [0, 4, 8]
    elif "sus2" in normalized:
        intervals = [0, 2, 7]
    elif "sus" in normalized:
        intervals = [0, 5, 7]
    elif is_minor:
        intervals = [0, 3, 7]
    else:
        intervals = [0, 4, 7]

    if "maj7" in normalized:
        intervals.append(11)
    elif "7" in normalized:
        intervals.append(10)
    elif "6" in normalized:
        intervals.append(9)

    base = _TRACK_ROOT_BASE.get(track, 60)
    root_pitch = base + semitone
    return [
        pitch
        for pitch in (root_pitch + interval for interval in intervals)
        if 0 <= pitch <= 127
    ]


def export_chord_tracks_to_midi(
    track_chords: dict[str, list[dict[str, Any]]],
) -> bytes:
    """将多条音轨的和弦区间导出为标准多轨 MIDI 字节。"""
    try:
        import pretty_midi
    except ImportError as e:
        raise MidiExporterError("缺少 pretty_midi，无法导出 MIDI") from e

    midi = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    note_count = 0
    for track, chords in track_chords.items():
        instrument = pretty_midi.Instrument(
            program=_TRACK_PROGRAMS.get(track, 0),
            name=track.upper(),
        )
        for chord in chords:
            start = max(0.0, float(chord.get("start", 0.0)))
            end = max(0.0, float(chord.get("end", 0.0)))
            if end <= start:
                continue
            for pitch in _chord_pitches(chord, track):
                instrument.notes.append(
                    pretty_midi.Note(
                        velocity=90,
                        pitch=pitch,
                        start=start,
                        end=end,
                    )
                )
                note_count += 1
        if instrument.notes:
            midi.instruments.append(instrument)

    if note_count == 0:
        raise MidiExporterError("没有可导出的有效和弦数据")

    output = io.BytesIO()
    midi.write(output)
    return output.getvalue()


def export_to_midi(
    separation_result: SeparationResult,
    output_path: str | Path,
    start: float = 0.0,
    duration: float | None = None,
) -> None:
    """便捷函数：将分离结果导出为 MIDI 文件。"""
    exporter = MidiExporter()
    exporter.export(separation_result, output_path, start, duration)
