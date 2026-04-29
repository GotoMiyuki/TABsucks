"""节奏型识别模块。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class RhythmType(Enum):
    """常见节奏型枚举。"""

    PLAIN = "plain"  # 平拍（每拍均匀）
    SWING = "swing"  # 摇摆
    SHUFFLE = "shuffle"  # 洗牌
    POLKA = "polka"  # 波尔卡（1-2-1 节奏）
    WALTZ = "waltz"  # 华尔兹（三拍子）
    MARCH = "march"  # 行进
    BOSSA_NOVA = "bossa_nova"  # 巴萨诺瓦
    TANGO = "tango"  # 探戈


@dataclass(frozen=True)
class RhythmPattern:
    """识别出的节奏型。"""

    type: RhythmType
    confidence: float  # 置信度 0.0 ~ 1.0
    description: str  # 文字描述

    def is_confident(self, threshold: float = 0.7) -> bool:
        """判断是否足够置信。"""
        return self.confidence >= threshold


class RhythmAnalyzerError(Exception):
    """节奏分析失败时抛出。"""

    pass


class RhythmAnalyzer:
    """节奏型识别器，分析常见节奏模式。"""

    def __init__(self) -> None:
        """初始化节奏分析器。"""
        self._patterns: list[RhythmPattern] = []

    def analyze(self, beat_info) -> list[RhythmPattern]:
        """分析音频的节奏型。

        Args:
            beat_info: BeatInfo 对象（来自 BeatTracker）。

        Returns:
            RhythmPattern 列表，按置信度排序。

        Examples:
            >>> from src.audio.loader import load_audio
            >>> from src.analysis import BeatTracker, RhythmAnalyzer
            >>> audio = load_audio("song.mp3")
            >>> tracker = BeatTracker()
            >>> beat_info = tracker.track(audio)
            >>> analyzer = RhythmAnalyzer()
            >>> patterns = analyzer.analyze(beat_info)
            >>> print(patterns[0].type.value)
            'swing'
        """
        # TODO: 接入节奏型识别算法
        # 目前返回占位数据：plain 节奏
        bpm = getattr(beat_info, "bpm", 120.0)
        confidence = 0.5  # 占位置信度

        patterns = [
            RhythmPattern(
                type=RhythmType.PLAIN,
                confidence=confidence,
                description=f"基本平拍节奏，BPM={bpm:.1f}",
            )
        ]
        self._patterns = patterns
        return patterns

    def get_dominant_pattern(self) -> RhythmPattern | None:
        """获取置信度最高的节奏型。"""
        if not self._patterns:
            return None
        return max(self._patterns, key=lambda p: p.confidence)
