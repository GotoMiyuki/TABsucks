"""BTC-SL 插件测试。"""

from __future__ import annotations

import numpy as np
import pytest

from src.plugins.chord.btc_sl import idx2voca_chord, _run_length_encode


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
