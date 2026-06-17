"""调性分析模块：从低音进行推断调性。

三维证据融合：
  1. Krumhansl-Schmuckler 音级分布相关分析
  2. 终止式模式匹配
  3. 功能和声一致性分析
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.analysis.chord import ROOT_NOTES, ChordEvent


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyAnalysis:
    """调性分析结果。"""

    key: str
    """调性根音，如 "A", "C#"。"""

    mode: str
    """调式："major" 或 "minor"。"""

    confidence: float
    """综合置信度 0.0 ~ 1.0。"""

    ks_correlation: float
    """Krumhansl-Schmuckler 相关系数。"""

    cadence_type: str | None
    """终止式类型："authentic"/"plagal"/"half"/"deceptive"/None。"""

    functional_coherence: float
    """功能和声一致性得分 0.0 ~ 1.0。"""


# ---------------------------------------------------------------------------
# 维度 1: Krumhansl-Schmuckler
# ---------------------------------------------------------------------------

_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


def _build_chroma_vector(progression: list[ChordEvent]) -> np.ndarray:
    """从 bass progression 构建 12 维 chroma 向量（按时长加权）。"""
    chroma = np.zeros(12)
    for event in progression:
        if event.root in ("N", "X"):
            continue
        try:
            idx = ROOT_NOTES.index(event.root)
        except ValueError:
            continue
        chroma[idx] += event.duration
    total = chroma.sum()
    if total > 0:
        chroma /= total
    return chroma


def _ks_analyze(progression: list[ChordEvent]) -> tuple[str, str, float]:
    """Krumhansl-Schmuckler 调性检测。

    Returns:
        (key, mode, correlation_score)
    """
    chroma = _build_chroma_vector(progression)
    best_key = "C"
    best_mode = "major"
    best_corr = -1.0

    for shift in range(12):
        shifted = np.roll(chroma, -shift)
        corr_major = float(np.corrcoef(shifted, _MAJOR_PROFILE)[0, 1])
        corr_minor = float(np.corrcoef(shifted, _MINOR_PROFILE)[0, 1])

        if corr_major > best_corr:
            best_corr = corr_major
            best_key = ROOT_NOTES[shift]
            best_mode = "major"
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_key = ROOT_NOTES[shift]
            best_mode = "minor"

    return best_key, best_mode, best_corr


# ---------------------------------------------------------------------------
# 维度 2: 终止式模式匹配
# ---------------------------------------------------------------------------

_CADENCE_PATTERNS: dict[str, list[tuple[int, str]]] = {
    "authentic": [(7, "D"), (0, "T")],  # V → I
    "plagal": [(5, "S"), (0, "T")],  # IV → I
    "half": [(0, "T"), (7, "D")],  # I → V
    "deceptive": [(7, "D"), (9, "T")],  # V → vi
}

_CADENCE_CONFIDENCE: dict[str, float] = {
    "authentic": 0.9,
    "plagal": 0.7,
    "half": 0.6,
    "deceptive": 0.5,
}


def _cadence_analyze(
    progression: list[ChordEvent],
) -> tuple[str | None, str | None, float]:
    """终止式模式匹配。

    Returns:
        (key, cadence_type, confidence)
    """
    if len(progression) < 2:
        return None, None, 0.0

    tail = progression[-2:]

    best_key: str | None = None
    best_cadence: str | None = None
    best_confidence = 0.0

    for candidate_key_idx in range(12):
        for cadence_name, pattern in _CADENCE_PATTERNS.items():
            if len(tail) != len(pattern):
                continue
            match = True
            for event, (expected_semi, _) in zip(tail, pattern):
                if event.root in ("N", "X"):
                    match = False
                    break
                try:
                    event_semi = ROOT_NOTES.index(event.root)
                except ValueError:
                    match = False
                    break
                actual_offset = (event_semi - candidate_key_idx) % 12
                if actual_offset != expected_semi:
                    match = False
                    break
            if match:
                conf = _CADENCE_CONFIDENCE[cadence_name]
                if conf > best_confidence:
                    best_confidence = conf
                    best_key = ROOT_NOTES[candidate_key_idx]
                    best_cadence = cadence_name

    return best_key, best_cadence, best_confidence


# ---------------------------------------------------------------------------
# 维度 3: 功能和声分析
# ---------------------------------------------------------------------------

_MAJOR_FUNCTION: dict[int, str] = {
    0: "T",  # I
    2: "S",  # ii
    4: "T",  # iii
    5: "S",  # IV
    7: "D",  # V
    9: "T",  # vi
    11: "D",  # vii
}

_MINOR_FUNCTION: dict[int, str] = {
    0: "T",  # i
    2: "D",  # ii°
    3: "S",  # III
    5: "S",  # iv
    7: "D",  # v/V
    8: "T",  # VI
    10: "D",  # vii°
}

_GOOD_TRANSITIONS = {
    ("T", "S"),
    ("S", "D"),
    ("D", "T"),
    ("T", "T"),
    ("S", "S"),
    ("T", "D"),
}


def _functional_analyze(
    progression: list[ChordEvent], candidate_key: str, mode: str
) -> float:
    """功能和声一致性评分。

    Returns:
        coherence score 0.0 ~ 1.0
    """
    if len(progression) < 2:
        return 0.0

    key_idx = ROOT_NOTES.index(candidate_key)
    func_map = _MAJOR_FUNCTION if mode == "major" else _MINOR_FUNCTION

    functions: list[str | None] = []
    for event in progression:
        if event.root in ("N", "X"):
            functions.append(None)
            continue
        try:
            semi = ROOT_NOTES.index(event.root)
        except ValueError:
            functions.append(None)
            continue
        offset = (semi - key_idx) % 12
        functions.append(func_map.get(offset))

    good = 0
    total = 0
    for i in range(len(functions) - 1):
        if functions[i] is not None and functions[i + 1] is not None:
            total += 1
            if (functions[i], functions[i + 1]) in _GOOD_TRANSITIONS:
                good += 1

    return good / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# 加权融合
# ---------------------------------------------------------------------------

_KS_WEIGHT = 0.5
_CADENCE_WEIGHT = 0.3
_FUNCTIONAL_WEIGHT = 0.2


def analyze_key(progression: list[ChordEvent]) -> KeyAnalysis:
    """从 bass progression 推断调性（三维证据融合）。

    Args:
        progression: Bass progression（低音进行序列）。

    Returns:
        KeyAnalysis 包含调性、调式、置信度及各维度得分。
    """
    if not progression:
        return KeyAnalysis(
            key="N",
            mode="major",
            confidence=0.0,
            ks_correlation=0.0,
            cadence_type=None,
            functional_coherence=0.0,
        )

    # 维度 1
    ks_key, ks_mode, ks_corr = _ks_analyze(progression)

    # 维度 2
    cad_key, cad_type, cad_conf = _cadence_analyze(progression)

    # 维度 3: 用 K-S 的最佳候选评估功能一致性
    func_score = _functional_analyze(progression, ks_key, ks_mode)

    # 加权融合
    final_key = ks_key
    final_mode = ks_mode

    if cad_key is not None and cad_key == ks_key:
        confidence = (
            _KS_WEIGHT * max(ks_corr, 0)
            + _CADENCE_WEIGHT * cad_conf
            + _FUNCTIONAL_WEIGHT * func_score
        )
    else:
        confidence = _KS_WEIGHT * max(ks_corr, 0) + _FUNCTIONAL_WEIGHT * func_score

    confidence = min(max(confidence, 0.0), 1.0)

    return KeyAnalysis(
        key=final_key,
        mode=final_mode,
        confidence=confidence,
        ks_correlation=ks_corr,
        cadence_type=cad_type,
        functional_coherence=func_score,
    )
