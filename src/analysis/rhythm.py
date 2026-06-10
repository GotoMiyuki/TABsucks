"""通用节奏数据结构与归一化工具。

实际节奏检测（BPM、拍号、复杂度）由 plugins/rhythm/ 下的插件负责；
本模块只负责承载、归一化和将插件输出转换为结构化的 RhythmInfo。
深度节奏分析（deep_rhythm）预留了接口，等 Phase C 完成后接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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


@dataclass(frozen=True)
class RhythmInfo:
    """节奏分析结果容器（对标 BeatInfo / ChordEvent）。

    由 build_rhythm_info() 从插件输出归一化生成。
    """

    global_bpm: float | None = None
    time_signature: str = "4/4"
    complexity_score: float = 0.0
    needs_deep_analysis: bool = False
    bpm_map: list[tuple[float, float]] = field(default_factory=list)

    @property
    def beats_per_measure(self) -> int:
        """每小节拍数。"""
        try:
            return int(self.time_signature.split("/")[0])
        except (ValueError, IndexError):
            return 4

    @property
    def beat_duration(self) -> float:
        """每拍时长（秒）。"""
        if self.global_bpm is None or self.global_bpm <= 0:
            return 0.0
        return 60.0 / self.global_bpm


class RhythmAnalyzerError(Exception):
    """节奏分析失败时抛出。"""

    pass


def build_rhythm_info(rhythm_source: dict | object) -> RhythmInfo:
    """将插件输出归一化为 RhythmInfo。

    兼容 FoundationRhythmPlugin 的输出格式：

    - 包装 dict：``{"status": "success", "data": {"global_bpm": 120.0, ...}}``
    - 直接 dict：``{"global_bpm": 120.0, "time_signature_guess": "4/4", ...}``
    - 含 ``data`` 属性的对象

    Args:
        rhythm_source: 插件输出的 dict 或对象。

    Returns:
        RhythmInfo 实例。

    Raises:
        RhythmAnalyzerError: 输入格式无法识别。
    """
    data = _extract_data_dict(rhythm_source)

    return RhythmInfo(
        global_bpm=data.get("global_bpm"),
        time_signature=data.get("time_signature_guess", data.get("time_signature", "4/4")),
        complexity_score=float(data.get("complexity_score", 0.0)),
        needs_deep_analysis=bool(data.get("needs_deep_analysis", data.get("needs_deep_rhythm_analysis", False))),
        bpm_map=[tuple(pair) for pair in data.get("bpm_map", [])],
    )


def _extract_data_dict(source: dict | object) -> dict:
    if isinstance(source, dict):
        # 包装格式：{"status": "success", "data": {...}}
        if "data" in source and isinstance(source["data"], dict):
            return source["data"]
        # 直接格式：{"global_bpm": 120.0, ...}
        if "global_bpm" in source:
            return source
        raise RhythmAnalyzerError(
            f"无法识别的节奏数据格式，需要 'global_bpm' 或 'data' 键，"
            f"实际键: {list(source.keys())}"
        )

    for attr in ("data",):
        if hasattr(source, attr):
            value = getattr(source, attr)
            if isinstance(value, dict):
                return value

    raise RhythmAnalyzerError(
        "RhythmAnalyzer 不再负责从音频中检测节奏，请先使用 plugins/rhythm/ 下的插件获取结果。"
    )


class RhythmAnalyzer:
    """节奏数据归一化器（对标 BeatTracker / ChordAnalyzer）。

    实际节奏检测由 plugins/rhythm/ 下的各插件负责；
    本类接收插件输出并归一化为 RhythmInfo。

    RhythmPattern 和 get_dominant_pattern 保留用于
    从 RhythmInfo 中提取节奏型分类（等 deep_rhythm 接入后会更丰富）。
    """

    def __init__(self) -> None:
        self._patterns: list[RhythmPattern] = []

    def analyze(self, rhythm_source: dict | object) -> RhythmInfo:
        """将插件输出归一化为 RhythmInfo。

        Args:
            rhythm_source: 插件输出的 dict 或对象。

        Returns:
            RhythmInfo 实例。
        """
        info = build_rhythm_info(rhythm_source)

        # 基于拍号推断简单节奏型（等 deep_rhythm 接入后替换为真实推断）
        self._infer_patterns(info)

        return info

    def _infer_patterns(self, info: RhythmInfo) -> None:
        """基于 RhythmInfo 推断节奏型列表。目前仅做简单映射。"""
        self._patterns = []
        if not info.global_bpm:
            return

        bps = info.beats_per_measure
        if bps == 3:
            self._patterns.append(
                RhythmPattern(type=RhythmType.WALTZ, confidence=0.6, description="3 拍子")
            )
        else:
            self._patterns.append(
                RhythmPattern(type=RhythmType.PLAIN, confidence=0.5, description=f"{bps} 拍子")
            )

    def get_dominant_pattern(self) -> RhythmPattern | None:
        """获取置信度最高的节奏型。"""
        if not self._patterns:
            return None
        return max(self._patterns, key=lambda p: p.confidence)
