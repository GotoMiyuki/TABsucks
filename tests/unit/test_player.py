"""音频播放控制模块测试。"""

from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock, patch

# Mock sounddevice 模块（测试环境可能未安装）
mock_sd = MagicMock()
sys_modules_patch = patch.dict("sys.modules", {"sounddevice": mock_sd})
sys_modules_patch.start()

from src.audio.player import (
    AudioPlayer,
    CallbackStreamBackend,
    LoopRange,
    PlaybackProgress,
    PlaybackState,
    SimpleAudioPlayer,
)


# ---------------------------------------------------------------------------
# 测试数据 fixtures
# ---------------------------------------------------------------------------

class DummyAudioData:
    """模拟 AudioData 对象，避免引入实际音频加载依赖。"""

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
def mono_1s_44k() -> DummyAudioData:
    """1 秒单声道 44.1kHz 正弦波。"""
    t = np.linspace(0, 1.0, 44100, endpoint=False)
    samples = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    return DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)


@pytest.fixture
def stereo_1s_44k() -> DummyAudioData:
    """1 秒立体声 44.1kHz 正弦波。"""
    t = np.linspace(0, 1.0, 44100, endpoint=False)
    left = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    right = np.sin(2 * np.pi * 880 * t).astype(np.float32)
    samples = np.stack([left, right], axis=0)  # shape: (2, 44100)
    return DummyAudioData(samples=samples, sample_rate=44100, duration=1.0)


# ---------------------------------------------------------------------------
# Mock 后端（用于隔离 sounddevice）
# ---------------------------------------------------------------------------

class MockBackend:
    """可注入的 Mock 后端，完全不依赖 sounddevice。"""

    def __init__(self) -> None:
        self._data: np.ndarray | None = None
        self._sr: int = 44100
        self._rate: float = 1.0
        self._position: int = 0
        self._stopped = True
        self._calls: list[str] = []

    def configure(self, data: np.ndarray, sample_rate: int, playback_rate: float) -> None:
        self._data = data
        self._sr = sample_rate
        self._rate = playback_rate
        self._position = 0
        self._stopped = False
        self._calls.append("configure")

    def start_stream(self, sample_rate: int, channels: int, blocksize: int) -> None:
        self._calls.append("start_stream")

    def stop_stream(self) -> None:
        self._stopped = True
        self._calls.append("stop_stream")

    def is_stopped(self) -> bool:
        return self._stopped

    @property
    def position(self) -> int:
        return self._position

    def advance(self, frames: int) -> None:
        """模拟播放 N 帧，推进内部位置。"""
        if self._data is not None:
            self._position = min(self._position + frames, len(self._data))


# ---------------------------------------------------------------------------
# PlaybackState / PlaybackProgress / LoopRange 数据结构测试
# ---------------------------------------------------------------------------

class TestPlaybackState:
    """PlaybackState 枚举测试。"""

    def test_all_states_defined(self) -> None:
        expected = {PlaybackState.STOPPED, PlaybackState.PLAYING, PlaybackState.PAUSED}
        assert set(PlaybackState) == expected

    def test_states_are_auto_values(self) -> None:
        """状态值应为正整数且互不相同。"""
        values = [s.value for s in PlaybackState]
        assert len(values) == len(set(values))


class TestPlaybackProgress:
    """PlaybackProgress 数据类测试。"""

    def test_fields_accessible(self) -> None:
        p = PlaybackProgress(current_time=1.5, duration=10.0, position=66150, state=PlaybackState.PLAYING)
        assert p.current_time == 1.5
        assert p.duration == 10.0
        assert p.position == 66150
        assert p.state == PlaybackState.PLAYING


class TestLoopRange:
    """LoopRange 数据类测试。"""

    def test_valid_range(self) -> None:
        loop = LoopRange(start=1.0, end=3.0)
        assert loop.is_valid() is True
        assert loop.contains(1.5) is True
        assert loop.contains(0.5) is False
        assert loop.contains(3.5) is False

    def test_invalid_when_start_lte_zero(self) -> None:
        loop = LoopRange(start=0.0, end=3.0)
        assert loop.is_valid() is False

    def test_invalid_when_end_lte_start(self) -> None:
        loop = LoopRange(start=3.0, end=1.0)
        assert loop.is_valid() is False

    def test_invalid_when_equal(self) -> None:
        loop = LoopRange(start=2.0, end=2.0)
        assert loop.is_valid() is False


# ---------------------------------------------------------------------------
# AudioPlayer 核心逻辑测试
# ---------------------------------------------------------------------------

class TestAudioPlayerLoad:
    """AudioPlayer.load() 测试。"""

    def test_load_mono_stays_1d(self, mono_1s_44k: DummyAudioData) -> None:
        """单声道音频加载后 samples 应保持 1D。"""
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        assert player._samples is not None
        assert player._samples.ndim == 1
        assert player.duration == pytest.approx(1.0, rel=1e-3)
        assert player.sample_rate == 44100

    def test_load_stereo_becomes_mono(self, stereo_1s_44k: DummyAudioData) -> None:
        """立体声音频加载后应混音为单声道。"""
        player = AudioPlayer(backend=MockBackend())
        player.load(stereo_1s_44k)
        assert player._samples is not None
        assert player._samples.ndim == 1
        assert player._samples.shape[0] == 44100

    def test_load_resets_position_to_zero(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        assert player.current_time == pytest.approx(0.0)
        assert player.state == PlaybackState.STOPPED

    def test_load_accepts_duckTyped_object(self) -> None:
        """load() 应接受任何具有 samples/sample_rate/duration 属性的对象。"""
        player = AudioPlayer(backend=MockBackend())
        obj = MagicMock()
        obj.samples = np.zeros(22050, dtype=np.float32)
        obj.sample_rate = 22050
        obj.duration = 1.0
        player.load(obj)  # 不应抛异常
        assert player.duration == 1.0


class TestAudioPlayerPlayPause:
    """AudioPlayer 播放/暂停/停止测试。"""

    def test_play_without_load_raises(self) -> None:
        player = AudioPlayer(backend=MockBackend())
        with pytest.raises(RuntimeError, match="请先调用 load"):
            player.play()

    def test_play_idempotent(self, mono_1s_44k: DummyAudioData) -> None:
        """连续两次 play() 不应创建两个线程。"""
        backend = MockBackend()
        player = AudioPlayer(backend=backend)
        player.load(mono_1s_44k)
        with patch.object(player, "_playback_loop"):
            player.play()
            player.play()  # 第二次应直接返回
            assert player._play_thread is not None

    def test_pause_when_not_playing_is_noop(self, mono_1s_44k: DummyAudioData) -> None:
        """非播放状态下 pause() 应无操作。"""
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.pause()  # 不应抛异常

    def test_stop_when_not_playing_is_noop(self, mono_1s_44k: DummyAudioData) -> None:
        """非播放状态下 stop() 应无操作。"""
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.stop()  # 不应抛异常


class TestAudioPlayerSeek:
    """AudioPlayer.seek() 测试。"""

    def test_seek_within_bounds(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.seek(0.5)
        assert player.current_time == pytest.approx(0.5, rel=1e-2)

    def test_seek_negative_clamped_to_zero(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.seek(-5.0)
        assert player.current_time == pytest.approx(0.0)

    def test_seek_beyond_duration_clamped_to_end(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.seek(999.0)
        assert player.current_time == pytest.approx(player.duration, rel=1e-2)


class TestAudioPlayerRate:
    """AudioPlayer.set_rate() 测试。"""

    def test_valid_rate_accepted(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_rate(1.5)
        assert player.playback_rate == 1.5

    def test_out_of_range_rate_below_05_raises(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        with pytest.raises(ValueError, match="0.5 ~ 2.0"):
            player.set_rate(0.1)

    def test_out_of_range_rate_049_raises(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        with pytest.raises(ValueError, match="0.5 ~ 2.0"):
            player.set_rate(0.49)

    def test_out_of_range_rate_above_20_raises(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        with pytest.raises(ValueError, match="0.5 ~ 2.0"):
            player.set_rate(2.1)

    def test_boundary_rate_05_accepted(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_rate(0.5)
        assert player.playback_rate == 0.5

    def test_boundary_rate_20_accepted(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_rate(2.0)
        assert player.playback_rate == 2.0

    def test_boundary_rate_10_accepted(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_rate(1.0)
        assert player.playback_rate == 1.0


class TestAudioPlayerLoop:
    """AudioPlayer.set_loop() 测试。"""

    def test_valid_loop_set(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_loop(0.2, 0.8)
        assert player.loop is not None
        assert player.loop.start == 0.2
        assert player.loop.end == 0.8

    def test_none_clears_loop(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_loop(0.2, 0.8)
        player.set_loop(None, None)
        assert player.loop is None

    def test_invalid_loop_start_zero_raises(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        with pytest.raises(ValueError, match="无效的循环区间"):
            player.set_loop(0.0, 1.0)

    def test_invalid_loop_end_before_start_raises(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        with pytest.raises(ValueError, match="无效的循环区间"):
            player.set_loop(5.0, 2.0)

    def test_invalid_loop_negative_start_raises(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        with pytest.raises(ValueError, match="无效的循环区间"):
            player.set_loop(-1.0, 0.5)


class TestAudioPlayerVolume:
    """AudioPlayer.set_volume() 测试。"""

    def test_volume_clipped_to_1(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_volume(1.5)
        assert player._volume == 1.0

    def test_volume_clipped_to_0(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        player.set_volume(-0.5)
        assert player._volume == 0.0


class TestAudioPlayerProgress:
    """AudioPlayer 进度队列测试。"""

    def test_get_progress_initially_none(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        assert player.get_progress() is None

    def test_get_progressBlocking_returns_none_on_timeout(self, mono_1s_44k: DummyAudioData) -> None:
        player = AudioPlayer(backend=MockBackend())
        player.load(mono_1s_44k)
        result = player.get_progressBlocking(timeout=0.05)
        assert result is None


# ---------------------------------------------------------------------------
# SimpleAudioPlayer 测试（同步阻塞播放器）
# ---------------------------------------------------------------------------

class TestSimpleAudioPlayer:
    """SimpleAudioPlayer 测试。"""

    def test_load_mono(self, mono_1s_44k: DummyAudioData) -> None:
        player = SimpleAudioPlayer()
        player.load(mono_1s_44k)
        assert player._samples is not None
        assert player._samples.ndim == 1

    def test_load_stereo_to_mono(self, stereo_1s_44k: DummyAudioData) -> None:
        player = SimpleAudioPlayer()
        player.load(stereo_1s_44k)
        assert player._samples.ndim == 1

    def test_play_without_load_raises(self) -> None:
        player = SimpleAudioPlayer()
        with pytest.raises(RuntimeError, match="请先调用 load"):
            player.play()

    def test_stop_sets_not_playing(self, mono_1s_44k: DummyAudioData) -> None:
        player = SimpleAudioPlayer()
        player.load(mono_1s_44k)
        player.stop()
        assert player._is_playing is False


# ---------------------------------------------------------------------------
# CallbackStreamBackend 单元测试（逻辑验证，不依赖 sounddevice）
# ---------------------------------------------------------------------------

class TestCallbackStreamBackendLogic:
    """CallbackStreamBackend 逻辑测试（Mock sounddevice）。"""

    def test_configure_resets_state(self) -> None:
        backend = CallbackStreamBackend()
        data = np.zeros(44100, dtype=np.float32)
        backend.configure(data, sample_rate=44100, playback_rate=1.5)
        assert backend._position == 0
        assert backend._rate == 1.5
        assert backend.is_stopped() is False

    def test_stop_stream_sets_flag(self) -> None:
        backend = CallbackStreamBackend()
        backend.stop_stream()
        assert backend.is_stopped() is True

    def test_advance_position(self) -> None:
        backend = CallbackStreamBackend()
        data = np.zeros(44100, dtype=np.float32)
        backend.configure(data, 44100, 1.0)
        backend.advance_position(1024)
        assert backend._position == 1024
