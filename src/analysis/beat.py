"""通用节拍数据结构与轻量工具。

BPM 和拍号识别由 rhythm_foundation / utils 负责；
本模块只负责承载、归一化和基于已知节拍时间生成事件。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

TimeSignature = tuple[int, int]


def normalize_time_signature(
    value: TimeSignature | str | Sequence[int] | None,
    default: TimeSignature = (4, 4),
) -> TimeSignature:
    """把拍号归一化成 (beats_per_measure, beat_unit)。"""
    if value is None:
        return default

    if isinstance(value, str):
        parts = value.strip().split("/")
        if len(parts) != 2:
            raise BeatTrackerError(f"无效拍号格式: {value!r}")
        try:
            beats_per_measure = int(parts[0])
            beat_unit = int(parts[1])
        except ValueError as exc:
            raise BeatTrackerError(f"无效拍号格式: {value!r}") from exc
        return beats_per_measure, beat_unit

    if isinstance(value, Sequence):
        if len(value) != 2:
            raise BeatTrackerError(f"无效拍号格式: {value!r}")
        try:
            beats_per_measure = int(value[0])
            beat_unit = int(value[1])
        except (TypeError, ValueError) as exc:
            raise BeatTrackerError(f"无效拍号格式: {value!r}") from exc
        return beats_per_measure, beat_unit

    raise BeatTrackerError(f"无效拍号格式: {value!r}")


def build_beat_events(
    beat_times: Sequence[float],
    beats_per_measure: int = 4,
    start_beat_number: int = 1,
) -> list["BeatEvent"]:
    """根据节拍时间点构建 BeatEvent 列表。"""
    return [
        BeatEvent(
            time=float(time_point),
            beat_number=start_beat_number + index,
            beats_per_measure=beats_per_measure,
        )
        for index, time_point in enumerate(beat_times)
    ]


@dataclass(frozen=True)
class BeatEvent:
    """单个节拍事件。"""

    time: float  # 秒
    beat_number: int  # 拍序号（从 1 开始）
    beats_per_measure: int = 4

    @property
    def measure(self) -> int:
        """所属小节。"""
        return (self.beat_number - 1) // self.beats_per_measure + 1

    @property
    def beat_in_measure(self) -> int:
        """小节内第几拍。"""
        return ((self.beat_number - 1) % self.beats_per_measure) + 1


@dataclass(frozen=True)
class BeatInfo:
    """节拍分析结果。"""

    bpm: float | None = None  # 每分钟节拍数
    time_signature: TimeSignature = (4, 4)  # 拍号，如 (4, 4)
    beat_events: list[BeatEvent] = field(default_factory=list)  # 所有节拍时间点

    @property
    def beats_per_measure(self) -> int:
        """每小节拍数。"""
        return normalize_time_signature(self.time_signature)[0]

    @property
    def beat_duration(self) -> float:
        """每拍时长（秒）。"""
        if self.bpm is None or self.bpm <= 0:
            return 0.0
        return 60.0 / self.bpm

    @property
    def measure_duration(self) -> float:
        """每小节时长（秒）。"""
        return self.beat_duration * self.beats_per_measure


class BeatTrackerError(Exception):
    """节拍跟踪失败时抛出。"""

    pass


class BeatTracker:
    """轻量节拍构建器。

    该类不再从原始音频里做 BPM / 拍号识别；这些信息应来自
    src.plugins.rhythm.rhythm_foundation 和 src.plugins.rhythm.utils。
    """

    def __init__(
        self,
        bpm: float | None = None,
        time_signature: TimeSignature | str = (4, 4),
    ) -> None:
        """初始化节拍跟踪器。"""
        self._bpm: float | None = bpm
        self._time_signature: TimeSignature = normalize_time_signature(
            time_signature
        )

    def track(
        self,
        beat_source: object | Sequence[float],
        bpm: float | None = None,
        time_signature: TimeSignature | str | None = None,
    ) -> BeatInfo:
        """根据已知节拍时间构建节拍信息。

        Args:
            beat_source: 节拍时间序列，或包含 ``beat_times`` 属性的对象。
            bpm: 可选 BPM；不传则尝试从节拍间隔估计。
            time_signature: 可选拍号；不传则使用当前配置。

        Returns:
            BeatInfo，包含 BPM、拍号和所有节拍事件。

        Raises:
            BeatTrackerError: 输入无法转换为节拍时间序列。

        Examples:
            >>> from src.analysis import BeatTracker
            >>> tracker = BeatTracker()
            >>> info = tracker.track([0.0, 0.5, 1.0, 1.5], bpm=120.0)
            >>> print(info.beat_events[0].beat_in_measure)
        """
        beat_times = self._extract_beat_times(beat_source)
        resolved_time_signature = normalize_time_signature(
            time_signature, default=self._time_signature
        )
        resolved_bpm = self._resolve_bpm(beat_times, bpm)
        beat_events = build_beat_events(
            beat_times,
            beats_per_measure=resolved_time_signature[0],
        )

        self._bpm = resolved_bpm
        self._time_signature = resolved_time_signature

        return BeatInfo(
            bpm=resolved_bpm,
            time_signature=resolved_time_signature,
            beat_events=beat_events,
        )

    def estimate_time_signature(
        self,
        time_signature: TimeSignature | str | None = None,
    ) -> TimeSignature:
        """归一化拍号。

        实际的拍号识别应在 rhythm_foundation 中完成；这里仅负责
        统一外部传入的拍号格式。
        """
        return normalize_time_signature(time_signature, default=self._time_signature)

    def _extract_beat_times(self, beat_source: object | Sequence[float]) -> np.ndarray:
        if hasattr(beat_source, "beat_times"):
            beat_times = getattr(beat_source, "beat_times")
        elif isinstance(beat_source, np.ndarray):
            beat_times = beat_source
        elif isinstance(beat_source, Sequence):
            beat_times = beat_source
        else:
            raise BeatTrackerError(
                "BeatTracker 不再负责从音频中检测节拍，请先使用 rhythm_foundation/utils 获取 beat_times、bpm 和 time_signature。"
            )

        beat_times_array = np.asarray(beat_times, dtype=float).reshape(-1)
        if beat_times_array.size == 0:
            return beat_times_array
        if np.any(np.isnan(beat_times_array)):
            raise BeatTrackerError("节拍时间序列包含无效数值。")
        return beat_times_array

    def _resolve_bpm(
        self,
        beat_times: np.ndarray,
        bpm: float | None,
    ) -> float | None:
        if bpm is not None:
            if bpm <= 0:
                raise BeatTrackerError("BPM 必须大于 0。")
            return float(bpm)

        if self._bpm is not None:
            return float(self._bpm)

        if beat_times.size >= 2:
            intervals = np.diff(beat_times)
            positive_intervals = intervals[intervals > 0]
            if positive_intervals.size > 0:
                mean_interval = float(np.mean(positive_intervals))
                if mean_interval > 0:
                    return 60.0 / mean_interval

        return None
