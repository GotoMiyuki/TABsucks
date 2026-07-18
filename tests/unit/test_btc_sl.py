"""BTC-SL 插件测试。"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import torch

# 将 ChordMini 的 src 注入 TABsucks 的 src 包路径
_CHORDMINI_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "plugins", "chord", "external", "chordmini", "src")
)
import src as _tabsucks_src
if _CHORDMINI_SRC not in _tabsucks_src.__path__:
    _tabsucks_src.__path__.insert(0, _CHORDMINI_SRC)

from src.plugins.chord.btc_sl import idx2voca_chord, _run_length_encode
from src.kernel.core.resource_controller import ResourceController


class TestIdx2VocaChord:
    """170 类索引→和弦标签映射测试。"""

    def test_c_major(self) -> None:
        """索引 1 = C:maj → "C"。"""
        # root_idx=0, quality_idx=1 (maj)
        assert idx2voca_chord(1) == "C"

    def test_c_minor(self) -> None:
        """索引 0 = C:min。"""
        # root_idx=0, quality_idx=0 (min)
        assert idx2voca_chord(0) == "C:min"

    def test_a_minor(self) -> None:
        """索引 84 = A:min。"""
        # root_idx=9 (A), quality_idx=0 (min) → 9*14+0 = 126... 不对
        # A 是 index 9, 9*14=126, quality_idx=0 (min) → 126
        assert idx2voca_chord(126) == "A:min"

    def test_g_maj7(self) -> None:
        """G:maj7 → root_idx=7, quality_idx=8 (maj7) → 7*14+8 = 106。"""
        assert idx2voca_chord(106) == "G:maj7"

    def test_nochord(self) -> None:
        assert idx2voca_chord(169) == "N"

    def test_x(self) -> None:
        assert idx2voca_chord(168) == "X"

    def test_out_of_range(self) -> None:
        assert idx2voca_chord(200) == "X"
        assert idx2voca_chord(-1) == "X"

    def test_all_12_roots_have_major(self) -> None:
        """12 个根音的 major 和弦都能正确映射。"""
        roots = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        for root_idx, expected_root in enumerate(roots):
            idx = root_idx * 14 + 1  # quality_idx=1 is maj
            assert idx2voca_chord(idx) == expected_root


class TestRunLengthEncode:
    """游程编码测试。"""

    def test_single_segment(self) -> None:
        preds = np.array([1, 1, 1, 1])
        result = _run_length_encode(preds, hop_length=2048, sr=22050)
        assert len(result) == 1
        assert result[0]["chord"] == idx2voca_chord(1)
        assert result[0]["start"] == 0.0

    def test_multiple_segments(self) -> None:
        preds = np.array([0, 0, 1, 1, 2])
        result = _run_length_encode(preds, hop_length=2048, sr=22050)
        assert len(result) == 3
        assert result[0]["chord"] == idx2voca_chord(0)
        assert result[1]["chord"] == idx2voca_chord(1)
        assert result[2]["chord"] == idx2voca_chord(2)

    def test_empty(self) -> None:
        result = _run_length_encode(np.array([]), hop_length=2048, sr=22050)
        assert result == []

    def test_time_continuity(self) -> None:
        """每段的 end 应等于下一段的 start。"""
        preds = np.array([0, 0, 1, 1, 2, 2])
        result = _run_length_encode(preds, hop_length=2048, sr=22050)
        for i in range(len(result) - 1):
            assert result[i]["end"] == result[i + 1]["start"]


class TestBTCSLChordPluginExecute:
    """BTCSLChordPlugin.execute 流程测试（使用 mock 模型）。"""

    @pytest.fixture()
    def plugin_with_mock_model(self, monkeypatch):
        from src.plugins.chord.btc_sl import _setup_chordmini_imports
        _setup_chordmini_imports()

        from src.models.btc_model import BTC_model
        from src.models.common.config import ModelConfig

        config = ModelConfig()
        mock_model = BTC_model(config=config)
        mock_model.eval()

        from src.plugins.chord.btc_sl import BTCSLChordPlugin
        plugin = BTCSLChordPlugin()

        def mock_init(self, rc, checkpoint_path=None):
            device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
            model = mock_model.to(device)
            return model, 0.0, 1.0

        def mock_cqt(audio, sr):
            num_frames = max(1, len(audio) // 2048)
            return np.random.randn(num_frames, 144).astype(np.float32)

        def mock_sliding_windows(model, feature_matrix, mean, std, seq_len=108,
                                 batch_size=32, model_type="BTC", n_classes=170,
                                 **kwargs):
            n_frames = feature_matrix.shape[0]
            return np.random.randint(0, n_classes, size=n_frames, dtype=np.int64)

        monkeypatch.setattr(BTCSLChordPlugin, "_init_model", mock_init)
        monkeypatch.setattr("src.plugins.chord.btc_sl._extract_cqt_features", mock_cqt)
        monkeypatch.setattr("src.plugins.chord.btc_sl._predict_sliding_windows", mock_sliding_windows)
        return plugin

    def test_execute_returns_correct_format(self, plugin_with_mock_model) -> None:
        rc = ResourceController()
        audio = np.random.randn(22050 * 5).astype(np.float32) * 0.01
        rc.set_buffer("piano", audio)
        rc.set_metadata("sample_rate", 22050)

        result = plugin_with_mock_model.execute(rc, stem_name="piano")

        assert result["status"] == "success"
        assert result["stem"] == "piano"
        assert isinstance(result["data"], list)
        for chord_event in result["data"]:
            assert "start" in chord_event
            assert "end" in chord_event
            assert "chord" in chord_event

    def test_version_is_v2(self, plugin_with_mock_model) -> None:
        assert plugin_with_mock_model.version == "2.0.0"
