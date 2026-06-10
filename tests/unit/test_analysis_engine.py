"""AnalysisEngine 测试。"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from src.kernel.core.analysis_engine import AnalysisEngine, AnalysisEngineError, AnalysisResult
from src.kernel.core.plugin_manager import PluginManager
from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


# ---- Mock 插件 ----

class MockRhythmPlugin(Plugin):
    @property
    def name(self): return "rhythm_foundation"
    @property
    def version(self): return "0.0.1"
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
    def name(self): return "rhythm_foundation"
    @property
    def version(self): return "0.0.1"
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
    def name(self): return "chord_ismir2019"
    @property
    def version(self): return "0.0.1"
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


class MockBassRootPlugin(Plugin):
    @property
    def name(self): return "chord_bass_root"
    @property
    def version(self): return "0.0.1"
    def execute(self, rc, **kwargs):
        return {"status": "success", "root": "A"}


# ---- 辅助 ----

def _setup_engine(rhythm_plugin=None, chord_plugin=None, bass_plugin=None, with_stems=True):
    rc = ResourceController()
    rc.set_buffer("raw", np.zeros(44100 * 2))
    rc.set_metadata("sample_rate", 44100)

    if with_stems:
        for stem in ["vocals", "drums", "bass", "piano", "guitar", "other"]:
            rc.set_buffer(stem, np.zeros(44100))

    pm = PluginManager(rc)
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
        """完整流水线：节奏→和弦→bass_root。"""
        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            chord_plugin=MockChordPlugin(),
            bass_plugin=MockBassRootPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert isinstance(result, AnalysisResult)
        assert result.rhythm is not None
        assert result.rhythm.global_bpm == 120.0
        assert result.rhythm.time_signature == "4/4"
        assert result.bass_root == "A"
        assert "piano" in result.chord_events
        assert "guitar" in result.chord_events
        assert len(result.chord_events["piano"]) == 2
        assert result.chord_events["piano"][0].name == "Am7"

    def test_progress_callback(self) -> None:
        """应调用 progress_callback 报告各阶段。"""
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassRootPlugin(),
        )

        steps = []
        def callback(step, progress):
            steps.append(step)

        with patch.object(engine, "_run_separation", return_value=None):
            engine.run(progress_callback=callback)

        assert "rhythm" in steps
        assert "bass_root" in steps

    def test_no_plugins_still_works(self) -> None:
        """没有注册插件时，应返回默认值而不报错。"""
        engine, _, _ = _setup_engine(with_stems=True)

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert result.rhythm.global_bpm is None  # RhythmInfo 默认值
        assert result.bass_root == "N"
        # stems 存在但无插件，chord_events 应为空列表
        assert all(v == [] for v in result.chord_events.values())

    def test_deep_rhythm_trigger_skips_gracefully(self) -> None:
        """complexity > 0.6 时触发 deep_rhythm，但无插件时应跳过。"""
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockComplexRhythmPlugin(),
            bass_plugin=MockBassRootPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert result.rhythm.needs_deep_analysis is True
        assert result.rhythm.complexity_score == 0.85
        # 不应报错，deep_rhythm 不存在时静默跳过

    def test_result_stored_on_engine(self) -> None:
        """run() 的结果应存储在 engine.result 属性上。"""
        engine, _, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassRootPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            result = engine.run()

        assert engine.result is result

    def test_result_stored_in_rc(self) -> None:
        """run() 的结果应存入 RC metadata。"""
        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassRootPlugin(),
        )

        with patch.object(engine, "_run_separation", return_value=None):
            engine.run()

        assert rc.get_metadata("analysis_result") is not None

    def test_missing_raw_buffer_raises(self) -> None:
        """缺少 raw buffer 时应报错。"""
        rc = ResourceController()
        pm = PluginManager(rc)
        engine = AnalysisEngine(rc, pm)

        with pytest.raises(AnalysisEngineError):
            engine.run()

    def test_separation_bridge_writes_stems_to_rc(self) -> None:
        """分离步骤应将 stems 写入 RC buffer。"""
        mock_sep_result = MagicMock()
        mock_track = np.zeros(44100)
        mock_sep_result.get_track.return_value = mock_track
        mock_sep_result.sample_rate = 44100

        engine, rc, _ = _setup_engine(
            rhythm_plugin=MockRhythmPlugin(),
            bass_plugin=MockBassRootPlugin(),
            with_stems=False,  # 不预设 stems
        )

        # mock _run_separation 以避免实际调用分离器
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


class TestAnalysisResult:
    """AnalysisResult 数据结构测试。"""

    def test_default_values(self) -> None:
        result = AnalysisResult()
        assert result.rhythm is None
        assert result.chord_events == {}
        assert result.bass_root == "N"
        assert result.separation_result is None
