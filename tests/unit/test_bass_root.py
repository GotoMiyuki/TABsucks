"""Bass Root 插件测试。"""

from __future__ import annotations

import sys
import types
import numpy as np
import pytest
from unittest.mock import MagicMock


def _make_mock_librosa(pyin_return=None):
    """创建一个 mock librosa 模块。"""
    mock = types.ModuleType("librosa")
    mock.note_to_hz = lambda note: {"E1": 41.2, "E4": 329.6}.get(note, 440.0)
    mock.hz_to_midi = lambda hz: 69.0 + 12.0 * np.log2(np.asarray(hz, dtype=float) / 440.0)
    mock.midi_to_note = lambda midi, octave=False: ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][int(round(midi)) % 12]
    if pyin_return is not None:
        mock.pyin = MagicMock(return_value=pyin_return)
    else:
        mock.pyin = MagicMock(return_value=(np.array([]), np.array([], dtype=bool), np.array([])))
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
        assert plugin.version == "1.0.0"


class TestDetectRoot:
    """_detect_root 根音检测测试。"""

    def test_silent_audio_returns_n(self) -> None:
        """静音输入应返回 "N"（pyin 无 voiced 帧）。"""
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
        """440Hz 帧应检测为 A。"""
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
        """插件应实现 Plugin ABC 要求的接口。"""
        from src.plugins import Plugin
        from src.plugins.chord.bass_root import BassRootPlugin
        plugin = BassRootPlugin()
        assert isinstance(plugin, Plugin)
        assert hasattr(plugin, "execute")
        assert hasattr(plugin, "name")
        assert hasattr(plugin, "version")
