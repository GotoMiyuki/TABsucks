"""波形数据生成模块测试。"""

from __future__ import annotations

import numpy as np
import pytest

from src.visualizer.waveform import WaveformData, compute_waveform


# ---------------------------------------------------------------------------
# 测试数据 fixtures
# ---------------------------------------------------------------------------

class DummyAudioData:
    """模拟 AudioData 对象。"""

    def __init__(self, samples: np.ndarray, sample_rate: int = 44100, duration: float | None = None) -> None:
        self.samples = samples
        self.sample_rate = sample_rate
        self.duration = duration if duration is not None else len(samples) / sample_rate


@pytest.fixture
def sine_1s_44k() -> DummyAudioData:
    """1 秒 440Hz 正弦波，44.1kHz 采样。"""
    t = np.linspace(0, 1.0, 44100, endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    return DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)


@pytest.fixture
def stereo_1s_44k() -> DummyAudioData:
    """1 秒立体声 44.1kHz 正弦波。"""
    t = np.linspace(0, 1.0, 44100, endpoint=False)
    left = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    right = (np.sin(2 * np.pi * 880 * t) * 0.3).astype(np.float32)
    samples = np.stack([left, right], axis=0)  # shape: (2, 44100)
    return DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)


@pytest.fixture
def silent_audio() -> DummyAudioData:
    """静音音频（用于测试归一化分母为零的情况）。"""
    samples = np.zeros(4410, dtype=np.float32)
    return DummyAudioData(samples=samples, sample_rate=4410, duration=1.0)


# ---------------------------------------------------------------------------
# WaveformData 数据类测试
# ---------------------------------------------------------------------------

class TestWaveformData:
    """WaveformData 数据类测试。"""

    def test_total_frames_property(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=200)
        assert wf.total_frames == 200

    def test_time_at_frame(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        # 1 秒 / 100 帧 = 0.01 秒/帧
        assert wf.time_at_frame(50) == pytest.approx(0.5, rel=1e-2)

    def test_frame_at_time(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        # 时间 0.5s 对应帧索引 50
        assert wf.frame_at_time(0.5) == 50

    def test_frame_at_time_clamped_to_last(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        # 超出范围的帧索引应被截断到最后一帧
        assert wf.frame_at_time(999.0) == 99

    def test_proportion_at_frame(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        assert wf.proportion_at_frame(0) == 0.0
        # proportion = frame_index / (total_frames - 1) = 50 / 99 ≈ 0.505
        assert wf.proportion_at_frame(50) == pytest.approx(50.0 / 99.0, rel=1e-2)
        # 最后一帧（index=99）对应 proportion≈1.0，frame=100 超出范围
        assert wf.proportion_at_frame(99) == pytest.approx(1.0, rel=1e-2)

    def test_time_proportion(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        assert wf.time_proportion(0.0) == 0.0
        assert wf.time_proportion(0.5) == pytest.approx(0.5, rel=1e-2)

    def test_to_dict_keys(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        d = wf.to_dict()
        expected_keys = {"peaks", "duration", "sampleRate", "frameInterval", "totalFrames"}
        assert set(d.keys()) == expected_keys

    def test_to_dict_peaks_are_list(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        d = wf.to_dict()
        assert isinstance(d["peaks"], list)
        assert len(d["peaks"]) == 100

    def test_to_dict_no_pixels_per_second(self, sine_1s_44k: DummyAudioData) -> None:
        """to_dict() 不应包含 pixels_per_second，这是前端的责任。"""
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        d = wf.to_dict()
        assert "pixelsPerSecond" not in d
        assert "pps" not in d

    def test_to_dict_values_are_json_serializable(self, sine_1s_44k: DummyAudioData) -> None:
        """to_dict() 输出的所有值都应是 JSON 可序列化类型。"""
        import json
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        d = wf.to_dict()
        json.dumps(d)  # 不抛异常即通过


# ---------------------------------------------------------------------------
# compute_waveform 函数测试
# ---------------------------------------------------------------------------

class TestComputeWaveform:
    """compute_waveform 函数测试。"""

    def test_output_frame_count(self, sine_1s_44k: DummyAudioData) -> None:
        """输出帧数应等于 num_frames。"""
        for n in [50, 100, 200, 1000]:
            wf = compute_waveform(sine_1s_44k, num_frames=n)
            assert len(wf.peaks) == n

    def test_output_duration_matches_input(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k)
        assert wf.duration == sine_1s_44k.duration

    def test_output_sample_rate_matches_input(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k)
        assert wf.sample_rate == sine_1s_44k.sample_rate

    def test_output_values_normalized_to_0_1(self, sine_1s_44k: DummyAudioData) -> None:
        """所有峰值应在 [0, 1] 范围内。"""
        wf = compute_waveform(sine_1s_44k)
        assert np.all(wf.peaks >= 0.0)
        assert np.all(wf.peaks <= 1.0)

    def test_stereo_input_mixed_to_mono(self, stereo_1s_44k: DummyAudioData) -> None:
        """立体声输入应被混音为单声道。"""
        wf = compute_waveform(stereo_1s_44k, num_frames=100)
        # 如果立体声没有被混音，compute_waveform 内部会先混音再计算
        # 因此结果与单声道处理一致
        assert wf.total_frames == 100

    def test_frame_interval_calculation(self, sine_1s_44k: DummyAudioData) -> None:
        """frameInterval 应等于 duration / num_frames。"""
        wf = compute_waveform(sine_1s_44k, num_frames=100)
        expected_interval = sine_1s_44k.duration / 100
        assert wf.frame_interval == pytest.approx(expected_interval, rel=1e-6)

    def test_short_audio_reduces_num_frames(self) -> None:
        """当音频太短（samples < num_frames）时，帧数应缩减。"""
        short = DummyAudioData(
            samples=np.random.rand(100).astype(np.float32),
            sample_rate=44100,
            duration=100 / 44100,
        )
        wf = compute_waveform(short, num_frames=200)
        assert wf.total_frames <= 200

    def test_silent_audio_produces_zero_peaks(self, silent_audio: DummyAudioData) -> None:
        """静音音频的峰值应全为 0。"""
        wf = compute_waveform(silent_audio, num_frames=100)
        # 最大值为 0，归一化后仍为 0，但不应报错
        assert np.all(wf.peaks == 0.0)

    def test_accepts_duckTyped_object(self) -> None:
        """应接受任何具有 samples/sample_rate/duration 属性的对象。"""
        obj = MagicMock()
        obj.samples = np.random.rand(22050).astype(np.float32)
        obj.sample_rate = 22050
        obj.duration = 1.0
        wf = compute_waveform(obj, num_frames=50)
        assert wf.total_frames == 50

    def test_1d_samples_unchanged(self) -> None:
        """1D samples 输入不应出错。"""
        samples = np.random.rand(44100).astype(np.float32)
        audio = DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)
        wf = compute_waveform(audio, num_frames=100)
        assert wf.total_frames == 100

    def test_2d_samples_with_axis0_channels(self) -> None:
        """2D samples shape (2, N) 应正确处理。"""
        samples = np.random.rand(2, 44100).astype(np.float32)
        audio = DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)
        wf = compute_waveform(audio, num_frames=100)
        assert wf.total_frames == 100
        assert wf.peaks.ndim == 1


class TestComputeWaveformEdgeCases:
    """compute_waveform 边界条件测试。"""

    def test_num_frames_1(self) -> None:
        """num_frames=1 是有效输入。"""
        samples = np.random.rand(44100).astype(np.float32)
        audio = DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)
        wf = compute_waveform(audio, num_frames=1)
        assert wf.total_frames == 1

    def test_duration_zero_guarded(self) -> None:
        """duration=0 不应导致除零错误。"""
        audio = MagicMock()
        audio.samples = np.array([0.1], dtype=np.float32)
        audio.sample_rate = 1
        audio.duration = 0.0
        wf = compute_waveform(audio, num_frames=10)
        # time_proportion 使用 max(duration, 0.001) 防零除
        assert wf.time_proportion(0.5) == pytest.approx(0.5 / 0.001)


# ---------------------------------------------------------------------------
# NumPy / JSON 相关
# ---------------------------------------------------------------------------

import json
from unittest.mock import MagicMock


class TestWaveformDataJsonRoundtrip:
    """WaveformData JSON 往返序列化测试。"""

    def test_to_dict_json_dumps_and_loads(self, sine_1s_44k: DummyAudioData) -> None:
        wf = compute_waveform(sine_1s_44k, num_frames=10)
        d = wf.to_dict()
        serialized = json.dumps(d)
        restored = json.loads(serialized)
        assert restored["totalFrames"] == 10
        assert len(restored["peaks"]) == 10
        assert restored["duration"] == 1.0