"""和弦标签可视化数据生成模块测试。"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock

from src.visualizer.chord import ChordLabelData, build_chord_labels
from src.analysis.chord import ChordEvent, ChordAnalyzer


# ---------------------------------------------------------------------------
# 测试数据 fixtures
# ---------------------------------------------------------------------------

def make_chord_event(
    root: str = "C",
    quality: str = "",
    start: float = 0.0,
    end: float = 2.0,
) -> ChordEvent:
    """创建 ChordEvent 的辅助函数。"""
    return ChordEvent(root=root, quality=quality, start=start, end=end)


# ---------------------------------------------------------------------------
# ChordLabelData 数据类测试
# ---------------------------------------------------------------------------

class TestChordLabelData:
    """ChordLabelData 数据类测试。"""

    def test_to_dict_returns_list(self) -> None:
        chords = [make_chord_event("C", "", 0.0, 2.0)]
        cl = ChordLabelData(chords=chords, duration=2.0)
        result = cl.to_dict()
        assert isinstance(result, list)

    def test_to_dict_fields(self) -> None:
        chords = [make_chord_event("Am", "m7", 0.0, 2.5)]
        cl = ChordLabelData(chords=chords, duration=5.0)
        d = cl.to_dict()
        assert len(d) == 1
        assert "start" in d[0]
        assert "end" in d[0]
        assert "duration" in d[0]
        assert "name" in d[0]
        assert "root" in d[0]
        assert "quality" in d[0]
        assert "startProportion" in d[0]
        assert "durationProportion" in d[0]

    def test_name_property(self) -> None:
        """ChordEvent.name 应正确拼接 root + quality。"""
        chord = make_chord_event("F", "m", 0.0, 1.0)
        assert chord.name == "Fm"

    def test_duration_property(self) -> None:
        """ChordEvent.duration 应为 end - start。"""
        chord = make_chord_event("G", "7", 1.0, 4.0)
        assert chord.duration == 3.0

    def test_start_proportion(self) -> None:
        cl = ChordLabelData(chords=[make_chord_event("C", "", 0.5, 2.5)], duration=5.0)
        d = cl.to_dict()
        assert d[0]["startProportion"] == pytest.approx(0.1, rel=1e-2)

    def test_duration_proportion(self) -> None:
        """durationProportion 应等于 (end - start) / duration。"""
        cl = ChordLabelData(chords=[make_chord_event("D", "m", 1.0, 3.0)], duration=4.0)
        d = cl.to_dict()
        # (3.0 - 1.0) / 4.0 = 0.5
        assert d[0]["durationProportion"] == pytest.approx(0.5, rel=1e-2)

    def test_no_pixels_per_second_in_output(self) -> None:
        """输出中不应包含 pixels_per_second。"""
        chords = [make_chord_event("C", "", 0.0, 1.0)]
        cl = ChordLabelData(chords=chords, duration=1.0)
        d = cl.to_dict()
        assert "pixelsPerSecond" not in d[0]
        assert "pps" not in d[0]

    def test_empty_chords(self) -> None:
        """空和弦列表应返回空列表。"""
        cl = ChordLabelData(chords=[], duration=10.0)
        assert cl.to_dict() == []

    def test_multiple_chords(self) -> None:
        """多个和弦应按顺序返回。"""
        chords = [
            make_chord_event("C", "", 0.0, 2.0),
            make_chord_event("G", "7", 2.0, 4.0),
            make_chord_event("Am", "", 4.0, 6.0),
        ]
        cl = ChordLabelData(chords=chords, duration=6.0)
        d = cl.to_dict()
        assert len(d) == 3
        assert d[0]["name"] == "C"
        assert d[1]["name"] == "G7"
        assert d[2]["name"] == "Am"


# ---------------------------------------------------------------------------
# build_chord_labels 函数测试
# ---------------------------------------------------------------------------

class TestBuildChordLabels:
    """build_chord_labels 函数测试。"""

    def test_uses_provided_duration(self) -> None:
        chords = [make_chord_event("C", "", 0.0, 2.0)]
        cl = build_chord_labels(chords, duration=100.0)
        assert cl.duration == 100.0

    def test_duration_inferred_from_last_chord_end(self) -> None:
        """当 duration=None 时，从最后一个和弦的 end 推断。"""
        chords = [
            make_chord_event("C", "", 0.0, 3.0),
            make_chord_event("D", "", 3.0, 5.0),
        ]
        cl = build_chord_labels(chords, duration=None)
        assert cl.duration == 5.0

    def test_empty_chords_infers_zero_duration(self) -> None:
        """空和弦列表时 duration 设为 0.0。"""
        cl = build_chord_labels([], duration=None)
        assert cl.duration == 0.0


# ---------------------------------------------------------------------------
# 边界条件
# ---------------------------------------------------------------------------

class TestChordLabelDataEdgeCases:
    """ChordLabelData 边界条件测试。"""

    def test_zero_duration_guarded(self) -> None:
        """duration=0 时 proportion 计算应使用 max(duration, 0.001)。"""
        chords = [make_chord_event("C", "", 0.1, 0.5)]
        cl = ChordLabelData(chords=chords, duration=0.0)
        d = cl.to_dict()
        # startProportion = 0.1 / 0.001 = 100，safe division
        assert d[0]["startProportion"] == pytest.approx(0.1 / 0.001)

    def test_chord_at_start(self) -> None:
        """从 0 开始的和弦 proportion 应为 0。"""
        chords = [make_chord_event("C", "", 0.0, 1.0)]
        cl = ChordLabelData(chords=chords, duration=10.0)
        d = cl.to_dict()
        assert d[0]["startProportion"] == 0.0

    def test_chord_at_end(self) -> None:
        """时长等于总时长的和弦 proportion 应为 1。"""
        chords = [make_chord_event("C", "", 0.0, 10.0)]
        cl = ChordLabelData(chords=chords, duration=10.0)
        d = cl.to_dict()
        assert d[0]["startProportion"] == 0.0
        assert d[0]["durationProportion"] == 1.0

    def test_very_short_chord(self) -> None:
        """极短和弦（1ms）应仍能正确计算 proportion。"""
        chords = [make_chord_event("C", "", 0.0, 0.001)]
        cl = ChordLabelData(chords=chords, duration=1.0)
        d = cl.to_dict()
        assert d[0]["durationProportion"] == pytest.approx(0.001, rel=1e-3)