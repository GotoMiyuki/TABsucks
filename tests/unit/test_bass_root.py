"""Bass Progression 插件测试。"""

from __future__ import annotations

import sys
import types
from dataclasses import asdict

import numpy as np
import pytest
from unittest.mock import MagicMock

from src.analysis.chord import ChordEvent
from src.kernel.core.resource_controller import ResourceController


def _make_mock_librosa(pyin_return=None):
    """创建一个 mock librosa 模块。"""
    mock = types.ModuleType("librosa")
    mock.note_to_hz = lambda note: {"E1": 41.2, "E4": 329.6}.get(note, 440.0)
    mock.hz_to_midi = lambda hz: 69.0 + 12.0 * np.log2(np.asarray(hz, dtype=float) / 440.0)
    mock.midi_to_note = lambda midi, octave=False: [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
    ][int(round(midi)) % 12]
    mock.times_like = lambda x, sr=22050, hop_length=512: np.arange(len(x)) * hop_length / sr
    if pyin_return is not None:
        mock.pyin = MagicMock(return_value=pyin_return)
    else:
        mock.pyin = MagicMock(
            return_value=(np.array([]), np.array([], dtype=bool), np.array([]))
        )
    return mock


class TestBassRootPlugin:
    """BassRootPlugin 基本属性测试。"""

    def test_name(self) -> None:
        from src.plugins.chord.bass_root import BassRootPlugin
        plugin = BassRootPlugin()
        assert plugin.name == "chord_bass_root"

    def test_version(self) -> None:
        from src.plugins.chord.bass_root import BassRootPlugin
        plugin = BassRootPlugin()
        assert plugin.version == "2.0.0"


class TestDetectRoot:
    """_detect_root 根音检测测试（向后兼容）。"""

    def test_silent_audio_returns_n(self) -> None:
        mock_librosa = _make_mock_librosa()
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            silent = np.zeros(22050 * 2, dtype=np.float64)
            result = plugin._detect_root(silent, 22050)
            assert result == "N"
        finally:
            del sys.modules["librosa"]

    def test_a440_returns_a(self) -> None:
        sr = 22050
        n_frames = 100
        f0 = np.full(n_frames, 440.0)
        voiced = np.ones(n_frames, dtype=bool)
        mock_librosa = _make_mock_librosa(pyin_return=(f0, voiced, np.zeros(n_frames)))
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            audio = np.zeros(sr * 2, dtype=np.float64)
            result = plugin._detect_root(audio, sr)
            assert result == "A"
        finally:
            del sys.modules["librosa"]

    def test_plugin_has_correct_interface(self) -> None:
        from src.plugins import Plugin
        from src.plugins.chord.bass_root import BassRootPlugin
        plugin = BassRootPlugin()
        assert isinstance(plugin, Plugin)
        assert hasattr(plugin, "execute")
        assert hasattr(plugin, "name")
        assert hasattr(plugin, "version")


class TestDetectProgression:
    """_detect_progression 低音进行检测测试。"""

    def test_basic_progression(self) -> None:
        """按 beat 分段应输出 ChordEvent 列表。"""
        sr = 22050
        # 模拟 4 个 beat，前半段 A440Hz，后半段 E330Hz
        n_frames = 200
        # beat 时间戳: 0.0, 0.5, 1.0, 1.5, 2.0
        beat_timestamps = [0.0, 0.5, 1.0, 1.5, 2.0]

        # 帧时间: 0, 0.023, 0.046, ... (hop=512, sr=22050)
        frame_times = np.arange(n_frames) * 512 / sr
        f0 = np.zeros(n_frames)
        voiced = np.zeros(n_frames, dtype=bool)

        # 前 100 帧 = A440Hz (前 ~1.16s)
        f0[:100] = 440.0
        voiced[:100] = True
        # 后 100 帧 = E330Hz
        f0[100:] = 330.0
        voiced[100:] = True

        mock_librosa = _make_mock_librosa(pyin_return=(f0, voiced, np.zeros(n_frames)))
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            audio = np.zeros(sr * 2, dtype=np.float64)
            result = plugin._detect_progression(audio, sr, beat_timestamps)
            assert len(result) >= 1
            assert all(isinstance(ev, ChordEvent) for ev in result)
            assert all(ev.quality == "" for ev in result)
        finally:
            del sys.modules["librosa"]

    def test_merges_adjacent_same_root(self) -> None:
        """相邻同 root 的 beat 应合并。"""
        sr = 22050
        n_frames = 200
        beat_timestamps = [0.0, 0.5, 1.0, 1.5, 2.0]

        # 全部 A440Hz → 全部同一个 root
        f0 = np.full(n_frames, 440.0)
        voiced = np.ones(n_frames, dtype=bool)

        mock_librosa = _make_mock_librosa(pyin_return=(f0, voiced, np.zeros(n_frames)))
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            audio = np.zeros(sr * 2, dtype=np.float64)
            result = plugin._detect_progression(audio, sr, beat_timestamps)
            # 所有 beat 都是同一个 root → 应合并为 1 个事件
            assert len(result) == 1
            assert result[0].root == "A"
            assert result[0].start == 0.0
        finally:
            del sys.modules["librosa"]

    def test_silent_audio_returns_empty(self) -> None:
        """无 voiced 帧 → 空列表。"""
        mock_librosa = _make_mock_librosa()
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            audio = np.zeros(22050 * 2, dtype=np.float64)
            result = plugin._detect_progression(audio, 22050, [0.0, 0.5, 1.0])
            assert result == []
        finally:
            del sys.modules["librosa"]


class TestExecute:
    """execute 方法集成测试。"""

    @pytest.mark.parametrize("channel_first", [False, True])
    def test_stereo_bass_is_temporarily_mixed_to_mono(
        self,
        channel_first: bool,
    ) -> None:
        sr = 22050
        n_samples = sr * 2
        stereo = np.column_stack(
            (
                np.full(n_samples, 0.25, dtype=np.float32),
                np.full(n_samples, 0.75, dtype=np.float32),
            )
        )
        stored_bass = stereo.T if channel_first else stereo
        f0 = np.full(100, 110.0)
        voiced = np.ones(100, dtype=bool)
        mock_librosa = _make_mock_librosa(
            pyin_return=(f0, voiced, np.zeros(100))
        )
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin

            plugin = BassRootPlugin()
            rc = ResourceController()
            rc.set_buffer("bass", stored_bass)
            rc.set_metadata("sample_rate", sr)
            rc.set_metadata("beat_timestamps", [0.0, 0.5, 1.0])

            plugin.execute(rc)

            pyin_audio = mock_librosa.pyin.call_args.args[0]
            assert pyin_audio.shape == (n_samples,)
            assert pyin_audio.dtype == np.float32
            assert np.allclose(pyin_audio, 0.5)
            assert rc.get_buffer("bass") is stored_bass
            assert rc.get_buffer("bass").shape == stored_bass.shape
        finally:
            del sys.modules["librosa"]

    def test_returns_root_and_progression(self) -> None:
        """返回值应包含 root 和 bass_progression。"""
        sr = 22050
        n_frames = 100
        beat_timestamps = [0.0, 0.5, 1.0]
        f0 = np.full(n_frames, 440.0)
        voiced = np.ones(n_frames, dtype=bool)

        mock_librosa = _make_mock_librosa(pyin_return=(f0, voiced, np.zeros(n_frames)))
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            rc = ResourceController()
            rc.set_buffer("bass", np.zeros(sr * 2, dtype=np.float64))
            rc.set_metadata("sample_rate", sr)
            rc.set_metadata("beat_timestamps", beat_timestamps)

            result = plugin.execute(rc)
            assert result["status"] == "success"
            assert "root" in result
            assert "bass_progression" in result
            assert isinstance(result["bass_progression"], list)
        finally:
            del sys.modules["librosa"]

    def test_no_beats_fallback(self) -> None:
        """无 beat_timestamps → 退回单根音模式。"""
        sr = 22050
        n_frames = 100
        f0 = np.full(n_frames, 440.0)
        voiced = np.ones(n_frames, dtype=bool)

        mock_librosa = _make_mock_librosa(pyin_return=(f0, voiced, np.zeros(n_frames)))
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            rc = ResourceController()
            rc.set_buffer("bass", np.zeros(sr * 2, dtype=np.float64))
            rc.set_metadata("sample_rate", sr)
            # 不设置 beat_timestamps

            result = plugin.execute(rc)
            assert result["root"] == "A"
            assert result["bass_progression"] == []
        finally:
            del sys.modules["librosa"]

    def test_backward_compat_bass_root_in_rc(self) -> None:
        """RC 中应同时有 bass_root 和 bass_progression。"""
        sr = 22050
        n_frames = 100
        beat_timestamps = [0.0, 0.5, 1.0]
        f0 = np.full(n_frames, 440.0)
        voiced = np.ones(n_frames, dtype=bool)

        mock_librosa = _make_mock_librosa(pyin_return=(f0, voiced, np.zeros(n_frames)))
        sys.modules["librosa"] = mock_librosa
        try:
            from src.plugins.chord.bass_root import BassRootPlugin
            plugin = BassRootPlugin()
            rc = ResourceController()
            rc.set_buffer("bass", np.zeros(sr * 2, dtype=np.float64))
            rc.set_metadata("sample_rate", sr)
            rc.set_metadata("beat_timestamps", beat_timestamps)

            plugin.execute(rc)
            assert rc.get_metadata("bass_root") == "A"
            assert isinstance(rc.get_metadata("bass_progression"), list)
        finally:
            del sys.modules["librosa"]
