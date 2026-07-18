"""Refiner 和弦精炼模块测试。"""

from __future__ import annotations

import pytest

from src.analysis.chord import ChordEvent
from src.analysis.refiner import (
    snap_to_beats,
    merge_stem_chords,
    mark_inversions,
    refine,
    _nearest_beat,
    _merge_adjacent,
)


def _chord(root: str, start: float, end: float, quality: str = "") -> ChordEvent:
    return ChordEvent(root=root, quality=quality, start=start, end=end)


class TestNearestBeat:
    """_nearest_beat 工具函数。"""

    def test_exact_match(self) -> None:
        assert _nearest_beat(2.0, [0.0, 1.0, 2.0, 3.0]) == 2.0

    def test_closer_to_previous(self) -> None:
        assert _nearest_beat(1.3, [0.0, 1.0, 2.0, 3.0]) == 1.0

    def test_closer_to_next(self) -> None:
        assert _nearest_beat(1.7, [0.0, 1.0, 2.0, 3.0]) == 2.0


class TestMergeAdjacent:
    """_merge_adjacent 工具函数。"""

    def test_merge_same(self) -> None:
        events = [_chord("C", 0.0, 1.0), _chord("C", 1.0, 2.0)]
        merged = _merge_adjacent(events)
        assert len(merged) == 1
        assert merged[0].start == 0.0
        assert merged[0].end == 2.0

    def test_no_merge_different_root(self) -> None:
        events = [_chord("C", 0.0, 1.0), _chord("D", 1.0, 2.0)]
        merged = _merge_adjacent(events)
        assert len(merged) == 2

    def test_no_merge_different_quality(self) -> None:
        events = [_chord("C", 0.0, 1.0), _chord("C", 1.0, 2.0, quality="m")]
        merged = _merge_adjacent(events)
        assert len(merged) == 2

    def test_empty(self) -> None:
        assert _merge_adjacent([]) == []


class TestSnapToBeats:
    """snap_to_beats 节拍对齐。"""

    def test_basic_snap(self) -> None:
        """事件边界 snap 到最近 beat。"""
        events = [_chord("C", 0.1, 1.1), _chord("G", 1.1, 2.1)]
        beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
        result = snap_to_beats(events, beats)
        assert len(result) >= 1
        # 所有事件的 start/end 应该是 beat 时间点
        for ev in result:
            assert ev.start in beats
            assert ev.end in beats

    def test_merge_after_snap(self) -> None:
        """snap 后相邻同和弦应合并。"""
        events = [_chord("C", 0.1, 1.0), _chord("C", 1.0, 2.1)]
        beats = [0.0, 1.0, 2.0, 3.0]
        result = snap_to_beats(events, beats)
        # 应该合并为一个 C
        assert len(result) == 1
        assert result[0].root == "C"
        assert result[0].start == 0.0
        assert result[0].end == 2.0

    def test_empty_events(self) -> None:
        assert snap_to_beats([], [0.0, 1.0]) == []

    def test_empty_beats(self) -> None:
        events = [_chord("C", 0.0, 1.0)]
        assert snap_to_beats(events, []) == events


class TestMergeStemChords:
    """merge_stem_chords 多轨合并。"""

    def test_roots_agree_take_richer_quality(self) -> None:
        """root 一致 → 取 quality 更丰富的。"""
        piano = [_chord("C", 0.0, 2.0, "maj7")]
        guitar = [_chord("C", 0.0, 2.0, "")]
        result = merge_stem_chords(piano, guitar)
        assert len(result) == 1
        assert result[0].quality == "maj7"

    def test_roots_disagree_take_longer(self) -> None:
        """root 不一致 → 取 duration 更长的。"""
        piano = [_chord("C", 0.0, 2.0)]  # 2s
        guitar = [_chord("G", 0.0, 1.0)]  # 1s
        result = merge_stem_chords(piano, guitar)
        assert len(result) >= 1
        assert result[0].root == "C"  # piano 更长

    def test_one_empty(self) -> None:
        piano = [_chord("C", 0.0, 2.0)]
        result = merge_stem_chords(piano, [])
        assert len(result) == 1
        assert result[0].root == "C"

    def test_both_empty(self) -> None:
        assert merge_stem_chords([], []) == []


class TestMarkInversions:
    """mark_inversions 转位标记。"""

    def test_bass_differs_marks_slash(self) -> None:
        """chord=C, bass=E → C/E。"""
        chords = [_chord("C", 0.0, 2.0)]
        bass = [_chord("E", 0.0, 2.0)]
        result = mark_inversions(chords, bass)
        assert len(result) == 1
        assert result[0].root == "C"
        assert result[0].quality == "/E"
        assert result[0].name == "C/E"

    def test_bass_same_no_change(self) -> None:
        """chord=C, bass=C → 不变。"""
        chords = [_chord("C", 0.0, 2.0)]
        bass = [_chord("C", 0.0, 2.0)]
        result = mark_inversions(chords, bass)
        assert len(result) == 1
        assert result[0].root == "C"
        assert result[0].quality == ""

    def test_with_existing_quality(self) -> None:
        """chord=Cm7, bass=E → Cm7/E。"""
        chords = [_chord("C", 0.0, 2.0, quality="m7")]
        bass = [_chord("E", 0.0, 2.0)]
        result = mark_inversions(chords, bass)
        assert result[0].quality == "m7/E"
        assert result[0].name == "Cm7/E"

    def test_no_bass_overlap(self) -> None:
        """无 bass 重叠 → 不变。"""
        chords = [_chord("C", 0.0, 2.0)]
        bass = [_chord("E", 3.0, 5.0)]
        result = mark_inversions(chords, bass)
        assert result[0].quality == ""

    def test_empty_chords(self) -> None:
        assert mark_inversions([], [_chord("E", 0.0, 2.0)]) == []

    def test_empty_bass(self) -> None:
        chords = [_chord("C", 0.0, 2.0)]
        assert mark_inversions(chords, []) == chords


class TestRefinePipeline:
    """refine 完整流水线。"""

    def test_full_pipeline(self) -> None:
        """snap + merge + inversion marking。"""
        piano = [_chord("C", 0.1, 1.1), _chord("C", 1.1, 2.1)]
        guitar = [_chord("C", 0.05, 2.05)]
        bass = [_chord("E", 0.0, 2.0)]
        beats = [0.0, 1.0, 2.0, 3.0]

        result = refine({"piano": piano, "guitar": guitar}, beats, bass)
        assert len(result) >= 1
        # 应该被标记为 slash chord
        assert any("/E" in ev.quality for ev in result)

    def test_no_data(self) -> None:
        assert refine({}, [0.0, 1.0], []) == []
