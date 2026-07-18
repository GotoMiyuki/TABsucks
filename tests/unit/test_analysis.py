"""分析模块测试。"""

from __future__ import annotations

import numpy as np
import pytest

from src.analysis.chord import (
    ROOT_NOTES,
    CHORD_QUALITIES,
    ChordAnalyzer,
    ChordAnalyzerError,
    ChordEvent,
    normalize_chord_label,
    build_chord_events,
)
from src.analysis.beat import BeatTracker, BeatEvent, BeatInfo, BeatTrackerError
from src.analysis.rhythm import (
    RhythmAnalyzer,
    RhythmAnalyzerError,
    RhythmInfo,
    RhythmPattern,
    RhythmType,
    build_rhythm_info,
)


# ---- ChordEvent 测试 ----

class TestChordEvent:
    """ChordEvent 数据类测试。"""

    def test_name_property(self) -> None:
        chord = ChordEvent(root="A", quality="m7", start=0.0, end=4.0)
        assert chord.name == "Am7"

    def test_name_property_major(self) -> None:
        chord = ChordEvent(root="C", quality="", start=0.0, end=4.0)
        assert chord.name == "C"

    def test_duration_property(self) -> None:
        chord = ChordEvent(root="G", quality="7", start=1.0, end=5.0)
        assert chord.duration == 4.0


# ---- normalize_chord_label 测试 ----

class TestNormalizeChordLabel:
    """和弦标签归一化测试。"""

    def test_simple_major(self) -> None:
        assert normalize_chord_label("C") == ("C", "")

    def test_simple_minor(self) -> None:
        assert normalize_chord_label("Am") == ("A", "m")

    def test_seven_chord(self) -> None:
        assert normalize_chord_label("G7") == ("G", "7")

    def test_sharp_root(self) -> None:
        assert normalize_chord_label("C#dim") == ("C#", "dim")

    def test_colon_separator(self) -> None:
        assert normalize_chord_label("C:maj7") == ("C", "maj7")

    def test_colon_sharp(self) -> None:
        assert normalize_chord_label("G#:sus4") == ("G#", "sus4")

    def test_no_chord(self) -> None:
        assert normalize_chord_label("N") == ("N", "")

    def test_no_chord_x(self) -> None:
        assert normalize_chord_label("X") == ("X", "")

    def test_empty_string(self) -> None:
        assert normalize_chord_label("") == ("N", "")

    def test_complex_quality(self) -> None:
        assert normalize_chord_label("F#m7") == ("F#", "m7")

    def test_hdim7(self) -> None:
        assert normalize_chord_label("B:hdim7") == ("B", "hdim7")


# ---- build_chord_events 测试 ----

class TestBuildChordEvents:
    """build_chord_events 归一化测试。"""

    def test_ismir_format(self) -> None:
        """ISMIR2019 / BTC-SL 格式：start + end + chord。"""
        dicts = [
            {"start": 0.0, "end": 2.0, "chord": "Am7"},
            {"start": 2.0, "end": 4.0, "chord": "C:maj7"},
        ]
        events = build_chord_events(dicts)
        assert len(events) == 2
        assert events[0].root == "A"
        assert events[0].quality == "m7"
        assert events[0].start == 0.0
        assert events[0].end == 2.0
        assert events[1].root == "C"
        assert events[1].quality == "maj7"

    def test_foundation_format(self) -> None:
        """chord_foundation 格式：time + chord，自动推算 end。"""
        dicts = [
            {"time": 0.0, "chord": "C:maj7"},
            {"time": 2.5, "chord": "G7"},
            {"time": 5.0, "chord": "Am"},
        ]
        events = build_chord_events(dicts)
        assert len(events) == 3
        assert events[0].start == 0.0
        assert events[0].end == 2.5
        assert events[1].start == 2.5
        assert events[1].end == 5.0
        assert events[2].start == 5.0
        assert events[2].end == 7.0  # 默认 +2.0

    def test_empty_list(self) -> None:
        assert build_chord_events([]) == []

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ChordAnalyzerError):
            build_chord_events([{"foo": "bar"}])


# ---- ChordAnalyzer 测试 ----

class TestChordAnalyzer:
    """ChordAnalyzer 归一化器测试。"""

    def test_analyze_dict_list(self) -> None:
        """analyze 接收 dict 列表。"""
        analyzer = ChordAnalyzer()
        dicts = [
            {"start": 0.0, "end": 2.0, "chord": "Am7"},
            {"start": 2.0, "end": 4.0, "chord": "C"},
        ]
        events = analyzer.analyze(dicts)
        assert len(events) == 2
        assert events[0].name == "Am7"
        assert events[1].name == "C"

    def test_analyze_object_with_chords_attr(self) -> None:
        """analyze 接收含 chords 属性的对象。"""
        analyzer = ChordAnalyzer()

        class PluginResult:
            chords = [
                {"start": 0.0, "end": 1.0, "chord": "G"},
                {"start": 1.0, "end": 2.0, "chord": "D"},
            ]

        events = analyzer.analyze(PluginResult())
        assert len(events) == 2
        assert events[0].root == "G"

    def test_analyze_object_with_data_attr(self) -> None:
        """analyze 接收含 data 属性的对象（兼容 ismir2019 输出）。"""
        analyzer = ChordAnalyzer()

        class ISMIRResult:
            data = [{"start": 0.0, "end": 3.0, "chord": "F:m"}]

        events = analyzer.analyze(ISMIRResult())
        assert events[0].root == "F"
        assert events[0].quality == "m"

    def test_analyze_rejects_audio_object(self) -> None:
        """传入无 chords/data 属性的对象时应报错。"""
        analyzer = ChordAnalyzer()

        class MockAudio:
            duration = 10.0
            sample_rate = 44100

        with pytest.raises(ChordAnalyzerError):
            analyzer.analyze(MockAudio())

    def test_analyze_all_events_have_valid_roots(self) -> None:
        """所有事件的根音均在 ROOT_NOTES 或特殊标记中。"""
        analyzer = ChordAnalyzer()
        dicts = [
            {"start": 0.0, "end": 1.0, "chord": "C#dim"},
            {"start": 1.0, "end": 2.0, "chord": "N"},
            {"start": 2.0, "end": 3.0, "chord": "G:maj7"},
        ]
        events = analyzer.analyze(dicts)
        for event in events:
            assert event.root in (*ROOT_NOTES, "N", "X")


# ---- BeatEvent 测试 ----

class TestBeatEvent:
    """BeatEvent 数据类测试。"""

    def test_measure_4_4(self) -> None:
        beat = BeatEvent(time=1.0, beat_number=5)
        assert beat.measure == 2
        assert beat.beat_in_measure == 1

    def test_measure_waltz(self) -> None:
        beat1 = BeatEvent(time=0.0, beat_number=1, beats_per_measure=3)
        beat2 = BeatEvent(time=0.5, beat_number=2, beats_per_measure=3)
        beat3 = BeatEvent(time=1.0, beat_number=3, beats_per_measure=3)
        beat4 = BeatEvent(time=1.5, beat_number=4, beats_per_measure=3)

        assert beat1.beat_in_measure == 1
        assert beat2.beat_in_measure == 2
        assert beat3.beat_in_measure == 3
        assert beat4.beat_in_measure == 1
        assert beat4.measure == 2


# ---- BeatTracker 测试 ----

class TestBeatTracker:
    """BeatTracker 节拍跟踪器测试。"""

    def test_init(self) -> None:
        tracker = BeatTracker()
        assert tracker._bpm is None
        assert tracker._time_signature == (4, 4)

    def test_estimate_time_signature(self) -> None:
        tracker = BeatTracker(time_signature="3/4")
        sig = tracker.estimate_time_signature()
        assert sig == (3, 4)

    def test_track_builds_beat_info_from_times(self) -> None:
        tracker = BeatTracker()
        beat_info = tracker.track([0.0, 0.5, 1.0, 1.5], bpm=120.0, time_signature="3/4")

        assert isinstance(beat_info, BeatInfo)
        assert beat_info.bpm == 120.0
        assert beat_info.time_signature == (3, 4)
        assert len(beat_info.beat_events) == 4
        assert beat_info.beat_events[0].beat_in_measure == 1
        assert beat_info.beat_events[3].measure == 2

    def test_track_rejects_audio_detection(self) -> None:
        tracker = BeatTracker()

        class MockAudio:
            samples = np.zeros(44100)
            sample_rate = 44100

        with pytest.raises(BeatTrackerError):
            tracker.track(MockAudio())


# ---- RhythmAnalyzer 测试 ----

class TestBuildRhythmInfo:
    """build_rhythm_info 归一化测试。"""

    def test_wrapped_format(self) -> None:
        """FoundationRhythmPlugin 包装格式：{status, data}。"""
        raw = {
            "status": "success",
            "data": {
                "global_bpm": 128.0,
                "time_signature_guess": "4/4",
                "complexity_score": 0.3,
                "needs_deep_analysis": False,
                "bpm_map": [(0.0, 128.0)],
            },
        }
        info = build_rhythm_info(raw)
        assert info.global_bpm == 128.0
        assert info.time_signature == "4/4"
        assert info.complexity_score == 0.3
        assert info.needs_deep_analysis is False
        assert len(info.bpm_map) == 1

    def test_direct_format(self) -> None:
        """直接格式：{global_bpm, ...}。"""
        raw = {"global_bpm": 120.0, "time_signature_guess": "3/4"}
        info = build_rhythm_info(raw)
        assert info.global_bpm == 120.0
        assert info.time_signature == "3/4"

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(RhythmAnalyzerError):
            build_rhythm_info({"foo": "bar"})


class TestRhythmInfo:
    """RhythmInfo 数据结构测试。"""

    def test_default_values(self) -> None:
        info = RhythmInfo()
        assert info.global_bpm is None
        assert info.time_signature == "4/4"
        assert info.complexity_score == 0.0

    def test_beats_per_measure(self) -> None:
        assert RhythmInfo(time_signature="4/4").beats_per_measure == 4
        assert RhythmInfo(time_signature="3/4").beats_per_measure == 3
        assert RhythmInfo(time_signature="7/8").beats_per_measure == 7

    def test_beat_duration(self) -> None:
        info = RhythmInfo(global_bpm=120.0)
        assert abs(info.beat_duration - 0.5) < 1e-6

    def test_beat_duration_no_bpm(self) -> None:
        assert RhythmInfo().beat_duration == 0.0


class TestRhythmAnalyzer:
    """RhythmAnalyzer 归一化器测试。"""

    def test_analyze_dict(self) -> None:
        analyzer = RhythmAnalyzer()
        raw = {
            "status": "success",
            "data": {
                "global_bpm": 120.0,
                "time_signature_guess": "4/4",
                "complexity_score": 0.3,
                "needs_deep_analysis": False,
            },
        }
        info = analyzer.analyze(raw)
        assert isinstance(info, RhythmInfo)
        assert info.global_bpm == 120.0

    def test_analyze_infer_waltz_pattern(self) -> None:
        analyzer = RhythmAnalyzer()
        raw = {"global_bpm": 100.0, "time_signature_guess": "3/4"}
        analyzer.analyze(raw)
        dominant = analyzer.get_dominant_pattern()
        assert dominant is not None
        assert dominant.type == RhythmType.WALTZ

    def test_analyze_infer_plain_pattern(self) -> None:
        analyzer = RhythmAnalyzer()
        raw = {"global_bpm": 120.0, "time_signature_guess": "4/4"}
        analyzer.analyze(raw)
        dominant = analyzer.get_dominant_pattern()
        assert dominant is not None
        assert dominant.type == RhythmType.PLAIN

    def test_get_dominant_pattern_without_analyze(self) -> None:
        analyzer = RhythmAnalyzer()
        assert analyzer.get_dominant_pattern() is None
