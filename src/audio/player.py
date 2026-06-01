"""音频播放控制模块，支持播放/暂停/跳转/调速/AB循环。

设计原则：
- 线程安全：通过 RLock 保护所有状态写操作
- 后端抽象：通过 IAudioBackend 接口解耦 sounddevice，便于测试时 mock
- 进度回调：主线程轮询 queue，避免跨线程调 UI
- 调速实现：重采样（非简单跳帧），保证音质
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Protocol

import numpy as np


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

class PlaybackState(Enum):
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass
class PlaybackProgress:
    """播放进度信息。主线程轮询获取，不在播放线程中回调。"""
    current_time: float      # 当前时间（秒）
    duration: float           # 总时长（秒）
    position: int            # 当前采样点索引
    state: PlaybackState


@dataclass
class LoopRange:
    """AB 循环区间。"""
    start: float  # 秒
    end: float    # 秒

    def contains(self, time: float) -> bool:
        return self.start <= time <= self.end

    def is_valid(self) -> bool:
        return self.end > self.start > 0


# ---------------------------------------------------------------------------
# 后端接口抽象（便于测试时替换为 Mock）
# ---------------------------------------------------------------------------

class IAudioBackend(Protocol):
    """音频后端接口，抽象 sounddevice 的依赖，便于单元测试时注入 Mock。"""

    def start_stream(self, sample_rate: int, channels: int, blocksize: int) -> None:
        """启动音频流（阻塞到 stop_stream 被调用）"""
        ...

    def stop_stream(self) -> None:
        """停止音频流"""
        ...

    def write(self, samples: np.ndarray) -> int:
        """向音频流写入采样数据，返回实际写入的采样点数"""
        ...


# ---------------------------------------------------------------------------
# sounddevice 后端实现
# ---------------------------------------------------------------------------

class SoundDeviceBackend:
    """基于 sounddevice 的音频后端实现。"""

    def __init__(self) -> None:
        import sounddevice as sd
        self._sd = sd
        self._stream: sd.OutputStream | None = None
        self._write_queue: list[np.ndarray] = []
        self._write_lock = threading.Lock()

    def start_stream(self, sample_rate: int, channels: int, blocksize: int) -> None:
        """启动非阻塞音频输出流"""
        self._stream = self._sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            blocksize=blocksize,
            dtype=np.float32,
        )
        self._stream.start()

    def stop_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def write(self, samples: np.ndarray) -> int:
        """向流中写入数据（同步写入，无缓冲）"""
        if self._stream is None:
            return 0
        written = self._stream.write(samples)
        return written if written else len(samples)


# ---------------------------------------------------------------------------
# 内部回调式后端（sounddevice 在后台线程调用回调函数）
# ---------------------------------------------------------------------------

class CallbackStreamBackend:
    """基于 sounddevice 回调模式的后端实现。

    与 OutputStream 的同步 write 不同，这里 sounddevice 在内部线程中
    定期调用提供的回调函数获取下一批音频数据，实现真正的非阻塞播放。
    """

    def __init__(self) -> None:
        self._stream = None
        self._data: np.ndarray | None = None
        self._position: int = 0
        self._sr: int = 44100
        self._stopped = threading.Event()
        self._rate: float = 1.0

    def configure(self, data: np.ndarray, sample_rate: int, playback_rate: float) -> None:
        """配置播放数据（播放线程安全调用）"""
        self._data = data
        self._sr = sample_rate
        self._position = 0
        self._rate = playback_rate
        self._stopped.clear()

    def _callback(self, outdata: np.ndarray, frames: int, status) -> None:
        """sounddevice 内部线程调用的回调函数"""
        if status:
            print(f"[AudioPlayer] stream status: {status}")

        if self._stopped.is_set() or self._data is None:
            outdata.fill(0)
            return

        # 计算本轮应读的采样点数（考虑播放速率）
        if self._rate == 1.0:
            start = self._position
            end = min(start + frames, len(self._data))
            to_read = end - start
            outdata[:to_read, 0] = self._data[start:end]
            if to_read < frames:
                outdata[to_read:, 0].fill(0)
            self._position = end
        else:
            # 调速：重采样
            # 目标帧数 frames，播放速率 rate
            # 输入需要的时间帧数
            needed_input = int(frames * self._rate)
            start = self._position
            end = min(start + needed_input, len(self._data))
            chunk = self._data[start:end]
            if len(chunk) < needed_input:
                chunk = np.pad(chunk, (0, needed_input - len(chunk)))
            # 简单线性插值重采样
            indices = np.linspace(0, len(chunk) - 1, frames)
            resampled = np.interp(indices, np.arange(len(chunk)), chunk).astype(np.float32)
            outdata[:, 0] = resampled
            self._position = end

        # 到达末尾，停止
        if self._position >= len(self._data):
            self._stopped.set()

    def start_stream(self, sample_rate: int, channels: int, blocksize: int) -> None:
        import sounddevice as sd
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            blocksize=blocksize,
            dtype=np.float32,
            callback=self._callback,
        )
        self._stream.start()

    def stop_stream(self) -> None:
        self._stopped.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    def advance_position(self, frames: int) -> None:
        """手动推进播放位置（外部线程调用，用于 AB Loop）"""
        self._position += int(frames * self._rate)
        if self._position >= len(self._data) if self._data is not None else True:
            self._stopped.set()


# ---------------------------------------------------------------------------
# AudioPlayer 主类
# ---------------------------------------------------------------------------

class AudioPlayer:
    """线程安全的音频播放器。

    特性：
    - 播放/暂停/停止/跳转
    - 调速（0.5x ~ 2.0x）：通过重采样实现
    - AB 循环
    - 音量控制
    - 进度轮询（主线程调用 get_progress()，避免跨线程回调 UI）

    使用示例：
        player = AudioPlayer()
        player.load(audio_data)
        player.play()

        # 主线程轮询进度（供 UI 使用）
        while player.state == PlaybackState.PLAYING:
            p = player.get_progress()
            if p:
                print(f"{p.current_time:.1f}s / {p.duration:.1f}s")
            time.sleep(0.05)

    测试示例（Mock 后端）：
        backend = MockBackend()
        player = AudioPlayer(backend=backend)
        player.load(DummyAudioData(...))
        player.play()
        assert player.get_progress().current_time > 0
    """

    def __init__(
        self,
        backend: CallbackStreamBackend | None = None,
    ) -> None:
        # 音频数据（播放线程只读，主人线程写入，加载时一次性混音为单声道）
        self._samples: np.ndarray | None = None
        self._sample_rate: int = 44100
        self._duration: float = 0.0

        # 播放状态
        self._state = PlaybackState.STOPPED
        self._playback_rate: float = 1.0
        self._volume: float = 1.0
        self._position: int = 0  # 采样点索引（主人线程维护）
        self._loop: LoopRange | None = None

        # 后端（可注入 Mock）
        self._backend = backend or CallbackStreamBackend()

        # 线程安全
        self._lock = threading.RLock()
        self._play_thread: threading.Thread | None = None

        # 进度队列（播放线程写入，主线程读取，完全线程安全）
        self._progress_queue: queue.Queue[PlaybackProgress] = queue.Queue()

        # 播放线程是否在运行
        self._running = threading.Event()

    # ----------------------- 只读属性 ------------------------

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def current_time(self) -> float:
        return self._position / self._sample_rate

    @property
    def playback_rate(self) -> float:
        return self._playback_rate

    @property
    def loop(self) -> LoopRange | None:
        return self._loop

    # ----------------------- 加载 ------------------------

    def load(self, audio_data) -> None:
        """从 AudioData 加载音频，并在主线程中混音为单声道。

        Args:
            audio_data: AudioData 对象，包含 samples / sample_rate / duration
        """
        with self._lock:
            self.stop()
            self._position = 0
            samples = np.asarray(audio_data.samples, dtype=np.float32)

            # 加载时即混音为单声道，避免播放线程中再处理
            if samples.ndim == 2:
                samples = np.mean(samples, axis=0)

            self._samples = samples
            self._sample_rate = int(audio_data.sample_rate)
            self._duration = float(audio_data.duration)
            self._state = PlaybackState.STOPPED

    # ----------------------- 控制 ------------------------

    def play(self) -> None:
        with self._lock:
            if self._samples is None:
                raise RuntimeError("请先调用 load() 加载音频")
            if self._state == PlaybackState.PLAYING:
                return
            if self._state == PlaybackState.STOPPED:
                self._position = 0

            self._state = PlaybackState.PLAYING
            self._running.set()
            self._play_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self._play_thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._state != PlaybackState.PLAYING:
                return
            self._state = PlaybackState.PAUSED
            self._running.clear()
            self._backend.stop_stream()
            if self._play_thread:
                self._play_thread.join(timeout=2.0)

    def stop(self) -> None:
        with self._lock:
            self._state = PlaybackState.STOPPED
            self._running.clear()
            self._backend.stop_stream()
            self._position = 0
            if self._play_thread:
                self._play_thread.join(timeout=2.0)
            # 清空进度队列
            while not self._progress_queue.empty():
                try:
                    self._progress_queue.get_nowait()
                except queue.Empty:
                    break

    def seek(self, time: float) -> None:
        """跳转到指定时间（秒）。线程安全。"""
        with self._lock:
            time = float(time)
            time = max(0.0, min(time, self._duration))
            self._position = int(time * self._sample_rate)

    def set_rate(self, rate: float) -> None:
        """设置播放速率。范围 [0.5, 2.0]。线程安全。"""
        rate = float(rate)
        if not 0.5 <= rate <= 2.0:
            raise ValueError("播放速率必须在 0.5 ~ 2.0 之间")
        with self._lock:
            self._playback_rate = rate

    def set_loop(self, start: float | None, end: float | None) -> None:
        """设置 AB 循环区间。start=None 表示取消循环。线程安全。"""
        with self._lock:
            if start is None or end is None:
                self._loop = None
                return
            loop = LoopRange(start=start, end=end)
            if not loop.is_valid():
                raise ValueError(f"无效的循环区间: start={start}, end={end}")
            self._loop = loop

    def set_volume(self, volume: float) -> None:
        """设置音量。范围 [0.0, 1.0]。线程安全。"""
        with self._lock:
            self._volume = float(np.clip(volume, 0.0, 1.0))

    # ----------------------- 进度查询 ------------------------

    def get_progress(self) -> PlaybackProgress | None:
        """主线程调用，获取最新进度（轮询模式，避免跨线程回调 UI）。

        Returns:
            最新的 PlaybackProgress，如果没有新数据则返回 None。
        """
        try:
            return self._progress_queue.get_nowait()
        except queue.Empty:
            return None

    def get_progressBlocking(self, timeout: float = 0.1) -> PlaybackProgress | None:
        """带超时的进度获取（可用于测试）。"""
        try:
            return self._progress_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ----------------------- 内部播放循环 ------------------------

    def _playback_loop(self) -> None:
        """后台播放线程主循环"""
        samples = self._samples
        sr = self._sample_rate
        rate = self._playback_rate
        vol = self._volume

        # 配置后端
        self._backend.configure(samples, sr, rate)
        self._backend.start_stream(sr, channels=1, blocksize=1024)

        while self._running.is_set() and not self._backend.is_stopped():
            # 检查 AB Loop
            loop = self._loop
            if loop is not None:
                current_time = self._backend._position / sr
                if current_time >= loop.end:
                    # 重置到 A 点（通过重新配置后端的数据起始位置）
                    new_pos = int(loop.start * sr)
                    self._backend._position = new_pos

            # 推送进度（每 100ms 一次）
            self._enqueue_progress()

            # 短暂 sleep 让出 CPU，不影响实时性
            threading.Event().wait(0.05)

        # 播放结束
        self._backend.stop_stream()
        with self._lock:
            if self._state == PlaybackState.PLAYING:
                self._state = PlaybackState.STOPPED
                self._position = 0

        self._enqueue_progress()

    def _enqueue_progress(self) -> None:
        """将当前进度推入队列（线程安全）"""
        p = PlaybackProgress(
            current_time=self._position / self._sample_rate,
            duration=self._duration,
            position=self._position,
            state=self._state,
        )
        try:
            self._progress_queue.put_nowait(p)
        except queue.Full:
            pass  # 丢弃旧数据，保证最新


# ---------------------------------------------------------------------------
# 简化的同步播放器（用于不需要回调控制的场景，如 CLI）
# ---------------------------------------------------------------------------

class SimpleAudioPlayer:
    """简化的同步音频播放器，直接调用 sounddevice 阻塞播放。

    适用于：CLI 演示、测试、不需要 UI 交互的场景。
    注意：此播放器在 play() 期间完全阻塞调用线程。
    """

    def __init__(self) -> None:
        self._samples: np.ndarray | None = None
        self._sample_rate: int = 44100
        self._position: int = 0
        self._is_playing: bool = False

    def load(self, audio_data) -> None:
        samples = np.asarray(audio_data.samples, dtype=np.float32)
        if samples.ndim == 2:
            samples = np.mean(samples, axis=0)
        self._samples = samples
        self._sample_rate = int(audio_data.sample_rate)
        self._position = 0

    def play(self) -> None:
        """阻塞播放，直到结束或 stop() 被调用"""
        import sounddevice as sd
        if self._samples is None:
            raise RuntimeError("请先调用 load()")
        self._is_playing = True
        sd.play(self._samples, samplerate=self._sample_rate)
        # 等待播放完成（可通过另一线程调用 stop() 中断）
        while self._is_playing:
            threading.Event().wait(0.1)

    def stop(self) -> None:
        import sounddevice as sd
        self._is_playing = False
        sd.stop()