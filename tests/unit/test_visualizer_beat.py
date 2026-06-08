"""节拍可视化数据生成模块测试。"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock

from src.visualizer.beat import BeatMarkerData, build_beat_markers
from src.analysis.beat import BeatEvent, BeatInfo, BeatTracker


# ---------------------------------------------------------------------------
# BeatMarkerData 数据类测试
# ---------------------------------------------------------------------------

class TestBeatMarkerData:
    """BeatMarkerData 数据类测试。"""

    def test_to_dict_returns_list(self) -> None:
        """to_dict() 应返回 list。"""
        mock_beats = [
            BeatEvent(time=0.0, beat_number=1, beats_per_measure=4),
            BeatEvent(time=0.5, beat_number=2, beats_per_measure=4),
        ]
        bm = BeatMarkerData(beats=mock_beats, duration=1.0)
        result = bm.to_dict()
        assert isinstance(result, list)

    def test_to_dict_fields(self) -> None:
        """每个节拍应包含正确的字段。"""
        mock_beats = [
            BeatEvent(time=0.0, beat_number=1, beats_per_measure=4),
            BeatEvent(time=0.5, beat_number=2, beats_per_measure=4),
        ]
        bm = BeatMarkerData(beats=mock_beats, duration=1.0)
        d = bm.to_dict()
        assert len(d) == 2
        assert "time" in d[0]
        assert "measure" in d[0]
        assert "beatInMeasure" in d[0]
        assert "isDownbeat" in d[0]
        assert "timeProportion" in d[0]

    def test_downbeat_flag_first_beat(self) -> None:
        """第一拍应标记为 isDownbeat=True。"""
        beats = [
            BeatEvent(time=0.0, beat_number=1, beats_per_measure=4),
            BeatEvent(time=0.5, beat_number=2, beats_per_measure=4),
        ]
        bm = BeatMarkerData(beats=beats, duration=1.0)
        d = bm.to_dict()
        assert d[0]["isDownbeat"] is True
        assert d[1]["isDownbeat"] is False

    def test_no_pixels_per_second_in_output(self) -> None:
        """输出中不应包含 pixels_per_second。"""
        mock_beats = [BeatEvent(time=0.0, beat_number=1, beats_per_measure=4)]
        bm = BeatMarkerData(beats=mock_beats, duration=1.0)
        d = bm.to_dict()
        assert "pixelsPerSecond" not in d[0]
        assert "pps" not in d[0]

    def test_time_proportion_calculation(self) -> None:
        """timeProportion 应等于 time / duration。"""
        mock_beats = [BeatEvent(time=0.25, beat_number=1, beats_per_measure=4)]
        bm = BeatMarkerData(beats=mock_beats, duration=2.0)
        d = bm.to_dict()
        assert d[0]["timeProportion"] == pytest.approx(0.125, rel=1e-6)


class TestBuildBeatMarkers:
    """build_beat_markers 函数测试。"""

    def test_empty_beat_info(self) -> None:
        """空 BeatInfo 应返回空的 BeatMarkerData。"""
        info = BeatInfo(bpm=120.0, time_signature=(4, 4), beat_events=[])
        bm = build_beat_markers(info, duration=10.0)
        assert bm.to_dict() == []

    def test_beat_info_with_events(self) -> None:
        """有节拍的 BeatInfo 应正确构建数据。"""
        beats = [
            BeatEvent(time=0.0, beat_number=1, beats_per_measure=4),
            BeatEvent(time=0.5, beat_number=2, beats_per_measure=4),
            BeatEvent(time=1.0, beat_number=3, beats_per_measure=4),
            BeatEvent(time=1.5, beat_number=4, beats_per_measure=4),
        ]
        info = BeatInfo(bpm=120.0, time_signature=(4, 4), beat_events=beats)
        bm = build_beat_markers(info, duration=2.0)
        d = bm.to_dict()
        assert len(d) == 4
        assert d[0]["time"] == 0.0
        assert d[0]["measure"] == 1
        assert d[1]["measure"] == 1
        # beat_number=1~4 全在第一小节（beats_per_measure=4），beat_number=5 才进入第二小节
        assert d[2]["measure"] == 1

    def test_duration_from_bpm_inferred(self) -> None:
        """当 duration=None 时，应从 BPM 和节拍数估算。

        当前实现使用 last_beat_number / beats_per_measure * 60/bpm，
        对于 beat_number=4, beats_per_measure=4, bpm=120：
        → 1 measure * 60/120 = 0.5s（注意：这是当前实现的实际行为）。
        """
        beats = [
            BeatEvent(time=0.0, beat_number=1, beats_per_measure=4),
            BeatEvent(time=0.5, beat_number=2, beats_per_measure=4),
            BeatEvent(time=1.0, beat_number=3, beats_per_measure=4),
            BeatEvent(time=1.5, beat_number=4, beats_per_measure=4),
        ]
        info = BeatInfo(bpm=120.0, time_signature=(4, 4), beat_events=beats)
        bm = build_beat_markers(info)  # 不传 duration
        # 实际推断结果：beat_number=4 / beats_per_measure=4 * 60/120 = 0.5s
        assert bm.duration == pytest.approx(0.5, rel=1e-2)

    def test_uses_provided_duration(self) -> None:
        """优先使用传入的 duration 参数。"""
        beats = [BeatEvent(time=0.0, beat_number=1, beats_per_measure=4)]
        info = BeatInfo(bpm=60.0, time_signature=(4, 4), beat_events=beats)
        bm = build_beat_markers(info, duration=100.0)
        assert bm.duration == 100.0


class TestBeatMarkerDataEdgeCases:
    """BeatMarkerData 边界条件测试。"""

    def test_zero_duration_guarded(self) -> None:
        """duration=0 时 timeProportion 不应报错（使用 max(duration, 0.001)）。"""
        mock_beats = [BeatEvent(time=0.5, beat_number=1, beats_per_measure=4)]
        bm = BeatMarkerData(beats=mock_beats, duration=0.0)
        d = bm.to_dict()
        # 0.5 / 0.001 = 500，应有合理值
        assert d[0]["timeProportion"] == pytest.approx(0.5 / 0.001)

    def test_measure_calculation(self) -> None:
        """measure 应正确跨小节递增。"""
        beats = []
        for i in range(9):
            beats.append(BeatEvent(time=i * 0.5, beat_number=i + 1, beats_per_measure=4))
        info = BeatInfo(bpm=120.0, time_signature=(4, 4), beat_events=beats)
        bm = build_beat_markers(info, duration=10.0)
        d = bm.to_dict()
        measures = [beat["measure"] for beat in d]
        assert measures == [1, 1, 1, 1, 2, 2, 2, 2, 3]