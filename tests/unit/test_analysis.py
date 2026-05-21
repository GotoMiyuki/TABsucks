"""分析模块测试。"""

from __future__ import annotations

import numpy as np
import pytest

from src.analysis.chord import (
    ROOT_NOTES,
    CHORD_QUALITIES,
    ChordAnalyzer,
    ChordEvent,
)
from src.analysis.beat import BeatTracker, BeatEvent, BeatInfo, BeatTrackerError
from src.analysis.rhythm import (
    RhythmAnalyzer,
    RhythmPattern,
    RhythmType,
)


# ---- ChordEvent 测试 ----

class TestChordEvent:
    """ChordEvent 数据类测试。"""

    def test_name_property(self) -> None:
        """name 属性返回和弦名称。"""
        chord = ChordEvent(root="Am", quality="m7", start=0.0, end=4.0)
        assert chord.name == "Amm7"

    def test_name_property_major(self) -> None:
        """大三和弦根音无后缀。"""
        chord = ChordEvent(root="C", quality="", start=0.0, end=4.0)
        assert chord.name == "C"

    def test_duration_property(self) -> None:
        """duration 属性返回正确时长。"""
        chord = ChordEvent(root="G", quality="7", start=1.0, end=5.0)
        assert chord.duration == 4.0


# ---- ChordAnalyzer 测试 ----

class TestChordAnalyzer:
    """ChordAnalyzer 和弦分析器测试。"""

    def test_analyze_returns_list(self) -> None:
        """analyze 应返回非空列表。"""
        analyzer = ChordAnalyzer()
        # 传入 mock 数据
        class MockAudio:
            duration = 10.0
            sample_rate = 44100

        result = analyzer.analyze(MockAudio())
        assert isinstance(result, list)
        assert len(result) > 0

    def test_analyze_all_chords_have_required_fields(self) -> None:
        """所有返回的和弦事件均有必填字段。"""
        analyzer = ChordAnalyzer()

        class MockAudio:
            duration = 8.0
            sample_rate = 44100

        chords = analyzer.analyze(MockAudio())
        for chord in chords:
            assert hasattr(chord, "root")
            assert hasattr(chord, "quality")
            assert hasattr(chord, "start")
            assert hasattr(chord, "end")
            assert chord.start < chord.end

    def test_chord_roots_are_valid(self) -> None:
        """所有和弦根音均在 ROOT_NOTES 中。"""
        analyzer = ChordAnalyzer()

        class MockAudio:
            duration = 20.0
            sample_rate = 44100

        chords = analyzer.analyze(MockAudio())
        for chord in chords:
            assert chord.root in ROOT_NOTES


# ---- BeatEvent 测试 ----

class TestBeatEvent:
    """BeatEvent 数据类测试。"""

    def test_measure_4_4(self) -> None:
        """4/4 拍下的小节计算正确。"""
        beat = BeatEvent(time=1.0, beat_number=5)
        assert beat.measure == 2
        assert beat.beat_in_measure == 1

    def test_measure_waltz(self) -> None:
        """3/4 拍下的小节计算正确。"""
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
        """初始化后无状态。"""
        tracker = BeatTracker()
        assert tracker._bpm is None
        assert tracker._time_signature == (4, 4)

    def test_estimate_time_signature(self) -> None:
        """拍号归一化返回配置值。"""
        tracker = BeatTracker(time_signature="3/4")
        sig = tracker.estimate_time_signature()
        assert sig == (3, 4)

    def test_track_builds_beat_info_from_times(self) -> None:
        """track 根据 beat_times 构建 BeatInfo。"""
        tracker = BeatTracker()
        beat_info = tracker.track([0.0, 0.5, 1.0, 1.5], bpm=120.0, time_signature="3/4")

        assert isinstance(beat_info, BeatInfo)
        assert beat_info.bpm == 120.0
        assert beat_info.time_signature == (3, 4)
        assert len(beat_info.beat_events) == 4
        assert beat_info.beat_events[0].beat_in_measure == 1
        assert beat_info.beat_events[3].measure == 2

    def test_track_rejects_audio_detection(self) -> None:
        """传入音频对象时应提示检测职责已迁移。"""
        tracker = BeatTracker()

        class MockAudio:
            samples = np.zeros(44100)
            sample_rate = 44100

        with pytest.raises(BeatTrackerError):
            tracker.track(MockAudio())


# ---- RhythmAnalyzer 测试 ----

class TestRhythmAnalyzer:
    """RhythmAnalyzer 节奏分析器测试。"""

    def test_analyze_returns_list(self) -> None:
        """analyze 应返回非空列表。"""
        analyzer = RhythmAnalyzer()

        class MockBeatInfo:
            bpm = 120.0

        patterns = analyzer.analyze(MockBeatInfo())
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_get_dominant_pattern(self) -> None:
        """get_dominant_pattern 返回置信度最高的节奏型。"""
        analyzer = RhythmAnalyzer()

        class MockBeatInfo:
            bpm = 100.0

        analyzer.analyze(MockBeatInfo())
        dominant = analyzer.get_dominant_pattern()
        assert dominant is not None
        assert isinstance(dominant, RhythmPattern)
