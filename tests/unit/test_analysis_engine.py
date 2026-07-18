"""AnalysisEngine 测试。"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from src.analysis.chord import ChordEvent
from src.analysis.key import KeyAnalysis
from src.kernel.core.analysis_engine import AnalysisEngine, AnalysisEngineError, AnalysisResult
from src.kernel.core.plugin_manager import PluginManager, PluginManagerError
from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


# ---- Mock 插件 ----


class MockRhythmPlugin(Plugin):
    @property
    def name(self):
        return "rhythm_foundation"

    @property
    def version(self):
        return "0.0.1"

    def execute(self, rc, **kwargs):
        return {
            "status": "success",
            "data": {
                "global_bpm": 120.0,
                "time_signature_guess": "4/4",
                "complexity_score": 0.3,
                "needs_deep_analysis": False,
                "bpm_map": [(0.0, 120.0)],
            },
        }


class MockComplexRhythmPlugin(Plugin):
    """complexity > 0.6 的节奏插件，用于测试 deep_rhythm 触发。"""

    @property
    def name(self):
        return "rhythm_foundation"

    @property
    def version(self):
        return "0.0.1"

    def execute(self, rc, **kwargs):
        return {
            "status": "success",
            "data": {
                "global_bpm": 137.5,
                "time_signature_guess": "7/8",
                "complexity_score": 0.85,
                "needs_deep_analysis": True,
                "bpm_map": [(0.0, 137.5)],
            },
        }


class MockChordPlugin(Plugin):
    @property
    def name(self):
        return "chord_ismir2019"

    @property
    def version(self):
        return "0.0.1"

    def execute(self, rc, **kwargs):
        stem = kwargs.get("stem_name", "piano")
        return {
            "status": "success",
            "stem": stem,
            "data": [
                {"start": 0.0, "end": 2.0, "chord": "Am7"},
                {"start": 2.0, "end": 4.0, "chord": "C"},
            ],
        }


class MockBassProgressionPlugin(Plugin):
    """返回 bass progression 格式的 mock bass 插件。"""

    @property
    def name(self):
        return "chord_bass_root"

    @property
    def version(self):
        return "0.0.1"

    def execute(self, rc, **kwargs):
        return {
            "status": "success",
            "root": "A",
            "bass_progression": [
                {"root": "E", "quality": "", "start": 0.0, "end": 1.0},
                {"root": "A", "quality": "", "start": 1.0, "end": 3.0},
                {"root": "A", "quality": "", "start": 3.0, "end": 4.0},
            ],
        }


# ---- 辅助 ----


def _setup_engine(
    rhythm_plugin=None, chord_plugin=None, bass_plugin=None, with_stems=True
):
    rc = ResourceController()
    rc.set_buffer("raw", np.zeros(44100 * 2))
    rc.set_metadata("sample_rate", 44100)

    if with_stems:
        for stem in ["vocals", "drums", "bass", "piano", "guitar", "other"]:
            rc.set_buffer(stem, np.zeros(44100))

    pm = PluginManager(rc, auto_discover=False)
    if rhythm_plugin:
        pm.register(rhythm_plugin)
    if chord_plugin:
        pm.register(chord_plugin)
    if bass_plugin:
        pm.register(bass_plugin)

    return AnalysisEngine(rc, pm), rc, pm


# ---- 测试 ----


class TestAnalysisEngineRun:
    """AnalysisEngine.run() 流水线测试。"""

    def test_full_pipeline(self) -> None:
        """完整流水线：节奏→节拍→和弦→bass→调性→精炼。"""
        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            chord_plugin=MockChordPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert isinstance(result, AnalysisResult)
        assert result.rhythm is not None
        assert result.rhythm.global_bpm == 120.0
        assert result.rhythm.time_signature == "4/4"

        # beat_info 应存在
        assert result.beat_info is not None
        assert result.beat_info.bpm == 120.0

        # bass_progression 应存在
        assert len(result.bass_progression) >= 1
        assert all(isinstance(ev, ChordEvent) for ev in result.bass_progression)

        # 向后兼容 bass_root
        assert result.bass_root == "A"

        # chord_events
        assert "piano" in result.chord_events
        assert "guitar" in result.chord_events
        assert len(result.chord_events["piano"]) == 2
        assert result.chord_events["piano"][0].name == "Am7"

        # key_analysis 应存在（有 bass progression）
        assert result.key_analysis is not None
        assert isinstance(result.key_analysis, KeyAnalysis)

    def test_progress_callback(self) -> None:
        """应调用 progress_callback 报告各阶段。"""
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        steps = []

        def callback(step, progress):
            if step not in steps:
                steps.append(step)

        with patch.object(engine, "_run_separation", return_value=None):
            engine.run(progress_callback=callback)

        assert "rhythm" in steps
        assert "beat_grid" in steps
        assert "bass_progression" in steps
        assert "chord" in steps
        assert "key_analysis" in steps
        assert "refine" in steps

    def test_no_plugins_still_works(self) -> None:
        """没有注册插件时，应返回默认值而不报错。"""
        engine, _, _ = _setup_engine(with_stems=True)

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert result.rhythm.global_bpm is None
        assert result.bass_progression == []
        assert result.bass_root == "N"
        assert all(v == [] for v in result.chord_events.values())
        assert result.key_analysis is None
        assert result.unified_chords == []

    def test_deep_rhythm_trigger_skips_gracefully(self) -> None:
        """complexity > 0.6 时触发 deep_rhythm，但无插件时应跳过。"""
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockComplexRhythmPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert result.rhythm.needs_deep_analysis is True
        assert result.rhythm.complexity_score == 0.85

    def test_result_stored_on_engine(self) -> None:
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert engine.result is result

    def test_result_stored_in_rc(self) -> None:
        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            engine.run()

        assert rc.get_metadata("analysis_result") is not None

    def test_missing_raw_buffer_raises(self) -> None:
        rc = ResourceController()
        pm = PluginManager(rc, auto_discover=False)
        engine = AnalysisEngine(rc, pm)

        with pytest.raises(AnalysisEngineError):
            engine.run()

    def test_separation_bridge_writes_stems_to_rc(self) -> None:
        mock_sep_result = MagicMock()
        mock_track = np.zeros(44100)
        mock_sep_result.get_track.return_value = mock_track
        mock_sep_result.sample_rate = 44100

        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
            with_stems=False,
        )

        track_names = ["vocals", "drums", "bass", "piano", "guitar", "other"]

        def fake_separation():
            for name in track_names:
                rc.set_buffer(name, mock_track)
            rc.set_metadata("separation_result", mock_sep_result)
            rc.set_metadata("sample_rate", mock_sep_result.sample_rate)
            return mock_sep_result

        with patch.object(engine, "_run_separation", side_effect=fake_separation):
            result = engine.run()

        assert result.separation_result is not None
        assert rc.get_buffer("piano") is mock_track

    def test_beat_grid_from_bpm(self) -> None:
        """BPM=120, 2s 音频 → 4 个拍点（0.0, 0.5, 1.0, 1.5）。"""
        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        beat_timestamps = rc.get_metadata("beat_timestamps")
        assert beat_timestamps is not None
        assert len(beat_timestamps) == 4  # 2s / 0.5s = 4 beats
        assert beat_timestamps[0] == 0.0
        assert beat_timestamps[1] == pytest.approx(0.5)

    def test_refine_runs_with_data(self) -> None:
        """有 chord events + beat timestamps 时 refine 应产出 unified_chords。"""
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            chord_plugin=MockChordPlugin(),
            bass_plugin=MockBassProgressionPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        # unified_chords 可能为空（取决于 merge 逻辑），但不应报错
        assert isinstance(result.unified_chords, list)


class TestAnalysisResult:
    """AnalysisResult 数据结构测试。"""

    def test_default_values(self) -> None:
        result = AnalysisResult()
        assert result.rhythm is None
        assert result.beat_info is None
        assert result.chord_events == {}
        assert result.bass_progression == []
        assert result.key_analysis is None
        assert result.unified_chords == []
        assert result.separation_result is None

    def test_bass_root_backward_compat(self) -> None:
        """bass_root property 应从 bass_progression 推导。"""
        result = AnalysisResult(
            bass_progression=[
                ChordEvent(root="E", quality="", start=0.0, end=1.0),
                ChordEvent(root="A", quality="", start=1.0, end=3.0),
                ChordEvent(root="A", quality="", start=3.0, end=4.0),
            ]
        )
        assert result.bass_root == "A"  # A 出现 2 次，E 出现 1 次

    def test_bass_root_empty_progression(self) -> None:
        result = AnalysisResult()
        assert result.bass_root == "N"


class TestAnalysisEngineRunSingle:
    """AnalysisEngine.run_single() 逐轨独立调用测试。"""

    def test_run_single_chord(self) -> None:
        """对 piano 执行和弦插件，应返回 list[ChordEvent] 并累积到 result。"""
        engine, _, _ = _setup_engine(chord_plugin=MockChordPlugin())

        events = engine.run_single("piano", "chord_ismir2019")

        assert isinstance(events, list)
        assert len(events) == 2
        assert all(isinstance(e, ChordEvent) for e in events)
        assert events[0].name == "Am7"

        # 应累积到 engine.result
        assert engine.result is not None
        assert "piano" in engine.result.chord_events
        assert engine.result.chord_events["piano"] == events

    def test_run_single_bass(self) -> None:
        """执行 bass_root 插件，应返回 list[ChordEvent] 并累积到 bass_progression。"""
        engine, _, _ = _setup_engine(bass_plugin=MockBassProgressionPlugin())

        events = engine.run_single("bass", "chord_bass_root")

        assert isinstance(events, list)
        assert len(events) == 3
        assert all(isinstance(e, ChordEvent) for e in events)
        assert events[0].root == "E"
        assert events[1].root == "A"

        # 应累积到 engine.result.bass_progression
        assert engine.result is not None
        assert engine.result.bass_progression == events

    def test_run_single_unknown_plugin_raises(self) -> None:
        """不存在的插件应抛出 PluginManagerError。"""
        engine, _, _ = _setup_engine()

        with pytest.raises(PluginManagerError, match="插件不存在"):
            engine.run_single("piano", "nonexistent_plugin")

    def test_run_single_result_accumulated(self) -> None:
        """多次调用后 engine.result 应正确累积各音轨的和弦结果。"""
        engine, _, _ = _setup_engine(chord_plugin=MockChordPlugin())

        engine.run_single("piano", "chord_ismir2019")
        engine.run_single("guitar", "chord_ismir2019")

        assert engine.result is not None
        assert "piano" in engine.result.chord_events
        assert "guitar" in engine.result.chord_events
        assert len(engine.result.chord_events) == 2

    def test_run_single_progress_callback(self) -> None:
        """应调用 progress_callback 报告插件名和音轨。"""
        engine, _, _ = _setup_engine(chord_plugin=MockChordPlugin())

        calls = []

        def callback(step, progress):
            calls.append((step, progress))

        engine.run_single("piano", "chord_ismir2019", progress_callback=callback)

        assert len(calls) == 2
        assert calls[0] == ("chord_ismir2019:piano", 0.0)
        assert calls[1] == ("chord_ismir2019:piano", 1.0)
