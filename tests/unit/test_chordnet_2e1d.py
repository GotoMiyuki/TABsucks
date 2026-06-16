"""ChordNet (2E1D) 插件测试。"""

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

from src.kernel.core.resource_controller import ResourceController
from src.plugins.chord.chordnet_2e1d import ChordNet2E1DPlugin


class TestChordNet2E1DPluginProperties:
    """插件基本属性测试。"""

    def test_name(self) -> None:
        plugin = ChordNet2E1DPlugin()
        assert plugin.name == "chord_chordnet_2e1d"

    def test_version(self) -> None:
        plugin = ChordNet2E1DPlugin()
        assert plugin.version == "1.0.0"


class TestChordNet2E1DExecute:
    """execute 流程测试（使用 mock 模型）。"""

    @pytest.fixture()
    def plugin_with_mock_model(self, monkeypatch):
        """构造一个使用随机初始化模型的插件实例。"""
        # 确保 ChordMini 的 src.__path__ 已配置
        from src.plugins.chord.chordnet_2e1d import _setup_chordmini_imports
        _setup_chordmini_imports()

        from src.models.chord_net import ChordNet
        from src.models.common.config import get_chordnet_config

        config = get_chordnet_config()
        mock_model = ChordNet(**config.to_chordnet_kwargs())
        mock_model.eval()

        plugin = ChordNet2E1DPlugin()

        def mock_init(self, rc, checkpoint_path=None):
            device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
            model = mock_model.to(device)
            return model, 0.0, 1.0

        def mock_cqt(audio, sr):
            num_frames = max(1, len(audio) // 2048)
            return np.random.randn(num_frames, 144).astype(np.float32)

        def mock_sliding_windows(model, feature_matrix, mean, std, seq_len=108,
                                 batch_size=32, model_type="ChordNet", n_classes=170,
                                 **kwargs):
            n_frames = feature_matrix.shape[0]
            return np.random.randint(0, n_classes, size=n_frames, dtype=np.int64)

        monkeypatch.setattr(ChordNet2E1DPlugin, "_init_model", mock_init)
        monkeypatch.setattr("src.plugins.chord.chordnet_2e1d._extract_cqt_features", mock_cqt)
        monkeypatch.setattr("src.plugins.chord.chordnet_2e1d._predict_sliding_windows", mock_sliding_windows)
        return plugin

    def test_execute_returns_correct_format(self, plugin_with_mock_model) -> None:
        rc = ResourceController()
        # 5 秒音频 @ 22050 Hz
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
            assert chord_event["start"] < chord_event["end"]

    def test_execute_writes_metadata(self, plugin_with_mock_model) -> None:
        rc = ResourceController()
        audio = np.random.randn(22050 * 3).astype(np.float32) * 0.01
        rc.set_buffer("guitar", audio)
        rc.set_metadata("sample_rate", 22050)

        plugin_with_mock_model.execute(rc, stem_name="guitar")

        stored = rc.get_metadata("chord_raw_guitar")
        assert stored is not None
        assert isinstance(stored, list)

    def test_model_caching(self, plugin_with_mock_model) -> None:
        """两次 execute 应复用 RC 中缓存的模型。"""
        rc = ResourceController()
        audio = np.random.randn(22050 * 2).astype(np.float32) * 0.01
        rc.set_buffer("piano", audio)
        rc.set_metadata("sample_rate", 22050)

        plugin_with_mock_model.execute(rc, stem_name="piano")
        # 第二次调用不应报错
        result = plugin_with_mock_model.execute(rc, stem_name="piano")
        assert result["status"] == "success"
