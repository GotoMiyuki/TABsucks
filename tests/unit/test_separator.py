"""分离器模块测试。"""

from __future__ import annotations

import numpy as np
import pytest

from src.separation.separator import (
    Separator,
    SeparationResult,
    SeparatorError,
    SUPPORTED_MODELS,
    TrackId,
    TRACK_LABELS,
)


class TestTrackId:
    """TrackId 枚举测试。"""

    def test_all_six_tracks_defined(self) -> None:
        """6 个音轨类型均已定义。"""
        expected = {"vocals", "drums", "bass", "piano", "guitar", "other"}
        actual = {t.value for t in TrackId}
        assert expected == actual

    def test_track_labels_complete(self) -> None:
        """每个 TrackId 均有对应标签。"""
        for track_id in TrackId:
            assert track_id in TRACK_LABELS
            assert TRACK_LABELS[track_id]


class TestSeparationResult:
    """SeparationResult 数据类测试。"""

    def test_get_track_returns_correct_array(self) -> None:
        """get_track 返回对应音轨数组。"""
        n = 100
        result = SeparationResult(
            vocals=np.zeros(n),
            drums=np.ones(n),
            bass=np.full(n, 2.0),
            piano=np.full(n, 3.0),
            guitar=np.full(n, 4.0),
            other=np.full(n, 5.0),
            sample_rate=44100,
        )
        assert np.array_equal(result.get_track(TrackId.DRUMS), np.ones(n))
        assert np.array_equal(result.get_track(TrackId.BASS), np.full(n, 2.0))

    def test_immutable(self) -> None:
        """SeparationResult 不可修改。"""
        result = SeparationResult(
            vocals=np.zeros(10),
            drums=np.ones(10),
            bass=np.ones(10),
            piano=np.ones(10),
            guitar=np.ones(10),
            other=np.ones(10),
            sample_rate=44100,
        )
        with pytest.raises(Exception):  # frozen dataclass
            result.vocals[0] = 1.0  # type: ignore


class TestSeparator:
    """Separator 分离器测试。"""

    def test_init_default_model(self) -> None:
        """默认使用 BS-RoFormer 模型。"""
        sep = Separator()
        assert sep.model_name == "bs_roformer_ep_300_sdr_9"

    def test_init_custom_model(self) -> None:
        """可指定自定义模型。"""
        sep = Separator(model_name="htdemucs")
        assert sep.model_name == "htdemucs"

    def test_init_raises_on_unknown_model(self) -> None:
        """未知模型名称应抛出 SeparatorError。"""
        with pytest.raises(
            SeparatorError, match="不支持的模型"
        ):
            Separator(model_name="unknown_model_xyz")

    def test_supported_models_list(self) -> None:
        """SUPPORTED_MODELS 应包含至少一个模型。"""
        assert len(SUPPORTED_MODELS) >= 1
        assert "bs_roformer_ep_300_sdr_9" in SUPPORTED_MODELS
