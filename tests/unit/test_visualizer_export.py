"""可视化数据统一导出模块测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest
from unittest.mock import MagicMock

from src.visualizer.export import export_visualization_json


# ---------------------------------------------------------------------------
# 测试数据 fixtures
# ---------------------------------------------------------------------------

class DummyAudioData:
    def __init__(
        self,
        samples: np.ndarray,
        sample_rate: int = 44100,
        duration: float | None = None,
    ) -> None:
        self.samples = samples
        self.sample_rate = sample_rate
        self.duration = duration if duration is not None else len(samples) / sample_rate


@pytest.fixture
def dummy_audio() -> DummyAudioData:
    t = np.linspace(0, 1.0, 44100, endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    return DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)


# ---------------------------------------------------------------------------
# export_visualization_json 测试
# ---------------------------------------------------------------------------

class TestExportVisualizationJson:
    """export_visualization_json 函数测试。"""

    def test_returns_dict(self, dummy_audio: DummyAudioData) -> None:
        result = export_visualization_json(dummy_audio)
        assert isinstance(result, dict)

    def test_waveform_always_present(self, dummy_audio: DummyAudioData) -> None:
        """波形数据始终存在（是必选输出）。"""
        result = export_visualization_json(dummy_audio)
        assert "waveform" in result
        assert isinstance(result["waveform"], dict)
        assert "peaks" in result["waveform"]
        assert "duration" in result["waveform"]

    def test_waveform_keys(self, dummy_audio: DummyAudioData) -> None:
        """波形的结构应完整。"""
        result = export_visualization_json(dummy_audio)
        wf = result["waveform"]
        expected_keys = {"peaks", "duration", "sampleRate", "frameInterval", "totalFrames"}
        assert set(wf.keys()) == expected_keys

    def test_beats_absent_when_none(self, dummy_audio: DummyAudioData) -> None:
        """beat_info=None 时 beats 字段应为 None。"""
        result = export_visualization_json(dummy_audio, beat_info=None)
        assert result["beats"] is None

    def test_chords_absent_when_none(self, dummy_audio: DummyAudioData) -> None:
        """chord_events=None 时 chords 字段应为 None。"""
        result = export_visualization_json(dummy_audio, chord_events=None)
        assert result["chords"] is None

    def test_metadata_fields(self, dummy_audio: DummyAudioData) -> None:
        """metadata 应包含正确字段。"""
        result = export_visualization_json(dummy_audio)
        meta = result["metadata"]
        assert "duration" in meta
        assert "sampleRate" in meta
        assert "hasBeatData" in meta
        assert "hasChordData" in meta
        assert "exportedAt" in meta

    def test_has_beat_data_false_when_none(self, dummy_audio: DummyAudioData) -> None:
        result = export_visualization_json(dummy_audio, beat_info=None)
        assert result["metadata"]["hasBeatData"] is False

    def test_has_chord_data_false_when_none(self, dummy_audio: DummyAudioData) -> None:
        result = export_visualization_json(dummy_audio, chord_events=None)
        assert result["metadata"]["hasChordData"] is False

    def test_json_serializable(self, dummy_audio: DummyAudioData) -> None:
        """返回值应能被 json.dumps 序列化。"""
        result = export_visualization_json(dummy_audio)
        json.dumps(result)  # 不抛异常即通过

    def test_output_path_writes_file(self, dummy_audio: DummyAudioData, tmp_path: Path) -> None:
        """当指定 output_path 时，应创建文件并写入 JSON。"""
        output = tmp_path / "vis.json"
        export_visualization_json(dummy_audio, output_path=output)
        assert output.exists()
        with output.open() as f:
            data = json.load(f)
        assert "waveform" in data

    def test_output_path_json_valid(self, dummy_audio: DummyAudioData, tmp_path: Path) -> None:
        """写入文件的内容应是有效的 JSON。"""
        output = tmp_path / "vis.json"
        export_visualization_json(dummy_audio, output_path=output)
        with output.open() as f:
            content = f.read()
        restored = json.loads(content)
        assert restored["waveform"]["duration"] == dummy_audio.duration

    def test_num_waveform_frames_applied(self, dummy_audio: DummyAudioData) -> None:
        """num_waveform_frames 参数应传递给 compute_waveform。"""
        result = export_visualization_json(dummy_audio, num_waveform_frames=50)
        assert result["waveform"]["totalFrames"] == 50


class TestExportVisualizationJsonWithBeatData:
    """带真实节拍数据的导出测试。"""

    def test_with_real_beat_info(self, dummy_audio: DummyAudioData) -> None:
        from src.analysis.beat import BeatInfo, BeatEvent, BeatTracker

        tracker = BeatTracker(bpm=120.0, time_signature="4/4")
        beat_info = tracker.track([0.0, 0.5, 1.0, 1.5])
        result = export_visualization_json(dummy_audio, beat_info=beat_info)
        assert result["beats"] is not None
        assert isinstance(result["beats"], list)
        assert len(result["beats"]) == 4
        assert result["metadata"]["hasBeatData"] is True

    def test_beats_have_required_fields(self, dummy_audio: DummyAudioData) -> None:
        from src.analysis.beat import BeatInfo, BeatEvent, BeatTracker

        tracker = BeatTracker(bpm=120.0, time_signature="4/4")
        beat_info = tracker.track([0.0, 0.5, 1.0, 1.5])
        result = export_visualization_json(dummy_audio, beat_info=beat_info)
        beat = result["beats"][0]
        assert "time" in beat
        assert "measure" in beat
        assert "beatInMeasure" in beat
        assert "isDownbeat" in beat
        assert "timeProportion" in beat


class TestExportVisualizationJsonWithChordData:
    """带真实和弦数据的导出测试。"""

    def test_with_chord_events(self, dummy_audio: DummyAudioData) -> None:
        from src.analysis.chord import ChordEvent

        chords = [
            ChordEvent(root="C", quality="", start=0.0, end=2.0),
            ChordEvent(root="G", quality="7", start=2.0, end=4.0),
        ]
        result = export_visualization_json(dummy_audio, chord_events=chords)
        assert result["chords"] is not None
        assert len(result["chords"]) == 2
        assert result["metadata"]["hasChordData"] is True

    def test_chords_have_required_fields(self, dummy_audio: DummyAudioData) -> None:
        from src.analysis.chord import ChordEvent

        chords = [ChordEvent(root="C", quality="maj7", start=0.0, end=3.0)]
        result = export_visualization_json(dummy_audio, chord_events=chords)
        chord = result["chords"][0]
        assert "start" in chord
        assert "end" in chord
        assert "duration" in chord
        assert "name" in chord
        assert "root" in chord
        assert "quality" in chord
        assert "startProportion" in chord
        assert "durationProportion" in chord


class TestExportVisualizationJsonCombined:
    """波形 + 节拍 + 和弦同时存在的测试。"""

    def test_all_data_present_together(self, dummy_audio: DummyAudioData) -> None:
        from src.analysis.beat import BeatInfo, BeatEvent, BeatTracker
        from src.analysis.chord import ChordEvent

        tracker = BeatTracker(bpm=120.0, time_signature="4/4")
        beat_info = tracker.track([0.0, 0.5, 1.0, 1.5])
        chords = [ChordEvent(root="C", quality="", start=0.0, end=1.0)]

        result = export_visualization_json(dummy_audio, beat_info=beat_info, chord_events=chords)

        assert result["waveform"] is not None
        assert result["beats"] is not None
        assert result["chords"] is not None
        assert result["metadata"]["hasBeatData"] is True
        assert result["metadata"]["hasChordData"] is True

    def test_json_roundtrip_full_output(self, dummy_audio: DummyAudioData, tmp_path: Path) -> None:
        from src.analysis.beat import BeatInfo, BeatEvent, BeatTracker
        from src.analysis.chord import ChordEvent

        tracker = BeatTracker(bpm=120.0, time_signature="4/4")
        beat_info = tracker.track([0.0, 0.5, 1.0, 1.5])
        chords = [ChordEvent(root="C", quality="", start=0.0, end=1.0)]

        output = tmp_path / "full_vis.json"
        export_visualization_json(dummy_audio, beat_info=beat_info, chord_events=chords, output_path=output)

        with output.open() as f:
            restored = json.load(f)

        assert restored["waveform"]["totalFrames"] == 2000
        assert len(restored["beats"]) == 4
        assert len(restored["chords"]) == 1