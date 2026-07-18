"""通用和弦数据结构与归一化工具。

实际和弦识别由 plugins/chord/ 下的各插件负责；
本模块只负责承载、归一化和将插件输出转换为结构化的 ChEvent 列表。
"""

from __future__ import annotations

from collections.abc import Sequence
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


# 用于 normalize_chord_label 的根音前缀集合（按长度降序排列，保证先匹配长前缀）
_ROOT_PREFIXES = sorted(
    [r for r in ROOT_NOTES if len(r) > 1], key=len, reverse=True
) + sorted([r for r in ROOT_NOTES if len(r) == 1], key=len, reverse=True)


def normalize_chord_label(label: str) -> tuple[str, str]:
    """把和弦标签字符串拆分为 (root, quality)。

    Examples:
        >>> normalize_chord_label("Am7")
        ('A', 'm7')
        >>> normalize_chord_label("C#dim")
        ('C#', 'dim')
        >>> normalize_chord_label("C:maj7")
        ('C', 'maj7')
        >>> normalize_chord_label("N")
        ('N', '')
        >>> normalize_chord_label("G")
        ('G', '')
    """
    if not label or label in ("N", "X"):
        return (label or "N", "")

    # 统一把冒号分隔符替换掉，方便解析 "C:maj7" → "Cmaj7"
    label = label.replace(":", "").replace("|", "").strip()

    # 尝试匹配根音前缀
    for prefix in _ROOT_PREFIXES:
        if label.startswith(prefix):
            quality = label[len(prefix):]
            return (prefix, quality)

    # 单字符 fallback：如果第一个字符是 A-G，直接当作根音
    if label and label[0] in "ABCDEFG":
        return (label[0], label[1:])

    # 无法识别
    return ("N", label)


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


def build_chord_events(chord_dicts: Sequence[dict]) -> list[ChordEvent]:
    """将插件输出的 dict 列表归一化为 ChordEvent 列表。

    支持两种插件输出格式：

    - ISMIR2019 / BTC-SL 格式：``{"start": float, "end": float, "chord": str}``
    - chord_foundation 格式：``{"time": float, "chord": str}``（自动推算 end）

    Args:
        chord_dicts: 插件输出的 dict 列表。

    Returns:
        ChordEvent 列表，按 start 时间排序。

    Raises:
        ChordAnalyzerError: 输入格式无法识别。
    """
    if not chord_dicts:
        return []

    events: list[ChordEvent] = []

    # 检测格式：看第一个元素有没有 "start" 键
    sample = chord_dicts[0]
    has_start_end = "start" in sample

    if has_start_end:
        for d in chord_dicts:
            root, quality = normalize_chord_label(d["chord"])
            events.append(
                ChordEvent(root=root, quality=quality, start=float(d["start"]), end=float(d["end"]))
            )
    elif "time" in sample:
        # 只有 time 的格式：通过相邻事件推算 end
        sorted_dicts = sorted(chord_dicts, key=lambda d: d["time"])
        for i, d in enumerate(sorted_dicts):
            root, quality = normalize_chord_label(d["chord"])
            start = float(d["time"])
            if i + 1 < len(sorted_dicts):
                end = float(sorted_dicts[i + 1]["time"])
            else:
                # 最后一个事件：给一个默认持续时长
                end = start + 2.0
            events.append(ChordEvent(root=root, quality=quality, start=start, end=end))
    else:
        raise ChordAnalyzerError(
            f"无法识别的和弦数据格式，需要 'start'/'end' 或 'time' 键，"
            f"实际键: {list(sample.keys())}"
        )

    return events


class ChordAnalyzer:
    """轻量和弦数据归一化器，类似 BeatTracker。

    实际和弦识别由 plugins/chord/ 下的各插件负责；
    本类只负责接收插件输出并归一化为 ChordEvent 列表。
    """

    def __init__(self, key: str | None = None) -> None:
        """初始化。

        Args:
            key: 可选调性根音（如 "C"、"Am"），暂未使用。
        """
        self._key = key

    def analyze(self, chord_source: Sequence[dict] | object) -> list[ChordEvent]:
        """将插件输出归一化为 ChordEvent 列表。

        Args:
            chord_source: 插件输出的 dict 列表，或含 ``chords`` / ``data``
                属性的对象。

        Returns:
            ChordEvent 列表，按 start 时间排序。

        Raises:
            ChordAnalyzerError: 输入格式无法识别。

        Examples:
            >>> analyzer = ChordAnalyzer()
            >>> events = analyzer.analyze([
            ...     {"start": 0.0, "end": 2.0, "chord": "Am7"},
            ...     {"start": 2.0, "end": 4.0, "chord": "C:maj7"},
            ... ])
            >>> print(events[0].name)
            Am7
        """
        chord_dicts = self._extract_chord_dicts(chord_source)
        return build_chord_events(chord_dicts)

    def analyze_with_key(
        self, chord_source: Sequence[dict] | object, key: str = "C"
    ) -> list[ChordEvent]:
        """分析并标注调内级数。

        Args:
            chord_source: 插件输出数据。
            key: 调性根音，如 "C"、"Am"。

        Returns:
            ChordEvent 列表。
        """
        self._key = key
        chords = self.analyze(chord_source)
        # TODO: 接入调性分析后更新 roman_numeral
        return chords

    @staticmethod
    def _extract_chord_dicts(chord_source: Sequence[dict] | object) -> Sequence[dict]:
        if isinstance(chord_source, Sequence) and not isinstance(chord_source, str):
            return chord_source

        for attr in ("chords", "data", "chord_sequence"):
            if hasattr(chord_source, attr):
                value = getattr(chord_source, attr)
                if isinstance(value, Sequence):
                    return value

        raise ChordAnalyzerError(
            "ChordAnalyzer 不再负责从音频中识别和弦，请先使用 plugins/chord/ 下的插件获取结果。"
        )
