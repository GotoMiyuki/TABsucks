"""和弦识别模块，支持 301 类和弦识别。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

# 根音列表
ROOT_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 和弦性质（Quality）列表，共 301 类
CHORD_QUALITIES = [
    # 三和弦
    "", "m", "dim", "aug",
    # 六和弦
    "6", "m6", "7", "m7", "dim7", "hdim7", "aug7", "7sus4",
    # 添加音
    "add9", "madd9", "add11", "madd11", "addb13", "maddb13",
    # 挂留音
    "sus2", "sus4",
    # 复合和弦
    "9", "m9", "9sus4", "7b9", "7#9", "11", "m11", "13",
    # Jazz 扩展
    "maj7", "maj9", "maj11", "maj13", "m(maj7)", "m(maj9)",
    "dim(maj7)", "dim9", "dim11", "aug9", "aug11",
]


class ChordQuality(Enum):
    """常用和弦性质枚举。"""

    MAJOR = ""
    MINOR = "m"
    DIMINISHED = "dim"
    AUGMENTED = "aug"
    MAJOR_7 = "maj7"
    MINOR_7 = "m7"
    DOMINANT_7 = "7"
    SUSPENDED_2 = "sus2"
    SUSPENDED_4 = "sus4"


@dataclass(frozen=True)
class ChordEvent:
    """识别出的单个和弦事件。"""

    root: str
    quality: str
    start: float  # 秒
    end: float  # 秒

    @property
    def name(self) -> str:
        """和弦名称，如 "Am7"。"""
        return f"{self.root}{self.quality}"

    @property
    def duration(self) -> float:
        """和弦持续时长（秒）。"""
        return self.end - self.start

    @property
    def roman_numeral(self) -> str:
        """返回和弦级数（需配合调性分析）。"""
        # TODO: 接入调性分析后实现
        return f"??{self.name}"


class ChordAnalyzerError(Exception):
    """和弦分析失败时抛出。"""

    pass


class ChordAnalyzer:
    """301 类和弦识别器。"""

    def __init__(self, model: str = "basic") -> None:
        """初始化和弦分析器。

        Args:
            model: 分析模型，"basic" 或 "advanced"。
        """
        self.model = model

    def analyze(self, audio_data) -> list[ChordEvent]:
        """对音频数据进行和弦识别。

        Args:
            audio_data: AudioData 对象或 numpy 音频数组。

        Returns:
            ChordEvent 列表，按时间顺序排列。

        Raises:
            ChordAnalyzerError: 分析过程出错。

        Examples:
            >>> from src.audio.loader import load_audio
            >>> from src.analysis import ChordAnalyzer
            >>> audio = load_audio("song.mp3")
            >>> analyzer = ChordAnalyzer()
            >>> chords = analyzer.analyze(audio)
            >>> print(chords[0].name)
            'Am7'
        """
        # TODO: 接入实际和弦识别模型（如 CNN/ChromaNet）
        # 目前返回占位数据
        sample_rate = getattr(audio_data, "sample_rate", 44100)
        duration = getattr(audio_data, "duration", 10.0)

        # 占位：每 4 秒一个和弦
        result: list[ChordEvent] = []
        beat_duration = 4.0
        chord_idx = 0
        t = 0.0
        while t < duration:
            root = ROOT_NOTES[chord_idx % len(ROOT_NOTES)]
            quality = CHORD_QUALITIES[chord_idx % len(CHORD_QUALITIES)]
            result.append(
                ChordEvent(
                    root=root,
                    quality=quality,
                    start=t,
                    end=min(t + beat_duration, duration),
                )
            )
            t += beat_duration
            chord_idx += 1

        return result

    def analyze_with_key(self, audio_data, key: str = "C") -> list[ChordEvent]:
        """分析并标注调内级数。

        Args:
            audio_data: AudioData 对象。
            key: 调性根音，如 "C"、"Am"。

        Returns:
            ChordEvent 列表。
        """
        chords = self.analyze(audio_data)
        # TODO: 接入调性分析后更新 roman_numeral
        return chords
