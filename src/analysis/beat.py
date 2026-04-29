"""节拍分析模块：BPM、拍号、节拍位置检测。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import librosa
import numpy as np

if TYPE_CHECKING:
    from src.audio.loader import AudioData


@dataclass(frozen=True)
class BeatEvent:
    """单个节拍事件。"""

    time: float  # 秒
    beat_number: int  # 拍序号（从 1 开始）

    @property
    def measure(self) -> int:
        """所属小节（假设 4/4 拍）。"""
        return (self.beat_number - 1) // 4 + 1

    @property
    def beat_in_measure(self) -> int:
        """小节内第几拍（1-4）。"""
        return ((self.beat_number - 1) % 4) + 1


@dataclass(frozen=True)
class BeatInfo:
    """节拍分析结果。"""

    bpm: float  # 每分钟节拍数
    time_signature: tuple[int, int]  # 拍号，如 (4, 4)
    beat_events: list[BeatEvent]  # 所有节拍时间点

    @property
    def beat_duration(self) -> float:
        """每拍时长（秒）。"""
        return 60.0 / self.bpm

    @property
    def measure_duration(self) -> float:
        """每小节时长（秒）。"""
        return self.beat_duration * self.time_signature[0]


class BeatTrackerError(Exception):
    """节拍跟踪失败时抛出。"""

    pass


class BeatTracker:
    """节拍跟踪器，检测音频的 BPM、拍号和节拍位置。"""

    def __init__(self) -> None:
        """初始化节拍跟踪器。"""
        self._bpm: float | None = None
        self._time_signature: tuple[int, int] = (4, 4)

    def track(self, audio_data: AudioData) -> BeatInfo:
        """分析音频的节拍信息。

        Args:
            audio_data: AudioData 对象。

        Returns:
            BeatInfo，包含 BPM、拍号和所有节拍事件。

        Raises:
            BeatTrackerError: 分析失败。

        Examples:
            >>> from src.audio.loader import load_audio
            >>> from src.analysis import BeatTracker
            >>> audio = load_audio("song.mp3")
            >>> tracker = BeatTracker()
            >>> info = tracker.track(audio)
            >>> print(f"BPM: {info.bpm}")
        """
        try:
            # 使用 librosa 进行节拍检测
            tempo, beat_frames = librosa.beat.beat_track(
                y=audio_data.samples,
                sr=audio_data.sample_rate,
                bpm=self._bpm,
            )
            beat_times = librosa.frames_to_time(
                beat_frames, sr=audio_data.sample_rate
            )

            self._bpm = float(tempo) if np.isscalar(tempo) else float(tempo[0])

            # 生成节拍事件列表
            beat_events = [
                BeatEvent(time=float(t), beat_number=i + 1)
                for i, t in enumerate(beat_times)
            ]

            return BeatInfo(
                bpm=self._bpm,
                time_signature=self._time_signature,
                beat_events=beat_events,
            )
        except Exception as e:
            raise BeatTrackerError(f"节拍分析失败: {e}") from e

    def estimate_time_signature(self, audio_data: AudioData) -> tuple[int, int]:
        """估算拍号。

        Args:
            audio_data: AudioData 对象。

        Returns:
            估算的拍号 (beats_per_measure, beat_unit)。
        """
        # TODO: 接入更复杂的拍号分析算法
        # 目前默认返回 4/4
        return (4, 4)
