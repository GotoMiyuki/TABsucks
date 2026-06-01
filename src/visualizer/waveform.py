"""波形数据生成模块。

设计原则：
- 纯函数，无状态
- 向量化计算，无 Python 循环（性能友好）
- 后端只返回物理量（时间/比例），pixels_per_second 由前端计算
- 输出 JSON-serializable 结构，供前端直接消费
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class WaveformData:
    """波形降采样数据。

    后端只提供物理量，前端负责映射到像素坐标。
    不包含 pixels_per_second，避免前后端耦合。

    Attributes:
        peaks: 归一化峰值数组，shape (N,)，范围 [0, 1]
        duration: 音频总时长（秒）
        sample_rate: 原始采样率（Hz）
        frame_interval: 每帧时长（秒），前端据此计算 x 坐标
        total_frames: 总帧数
    """

    peaks: np.ndarray          # shape: (N,)
    duration: float             # 秒
    sample_rate: int            # Hz
    frame_interval: float       # 秒/帧

    @property
    def total_frames(self) -> int:
        return len(self.peaks)

    def time_at_frame(self, frame_index: int) -> float:
        """帧索引 → 时间戳（秒）"""
        return frame_index * self.frame_interval

    def frame_at_time(self, time: float) -> int:
        """时间（秒） → 帧索引"""
        return min(int(time / self.frame_interval), len(self.peaks) - 1)

    def proportion_at_frame(self, frame_index: int) -> float:
        """帧索引 → 音频时间轴比例 [0, 1]，前端可直接乘 canvas_width"""
        return frame_index / max(self.total_frames - 1, 1)

    def time_proportion(self, time: float) -> float:
        """时间（秒） → 音频时间轴比例 [0, 1]"""
        return time / max(self.duration, 0.001)

    def to_dict(self) -> dict:
        """序列化为 JSON-serializable 字典，供前端直接使用。

        不包含 pixels_per_second，前端自行计算：
            x = time_proportion * canvas_width
        """
        return {
            "peaks": self.peaks.tolist(),
            "duration": self.duration,
            "sampleRate": self.sample_rate,
            "frameInterval": self.frame_interval,
            "totalFrames": self.total_frames,
        }


def compute_waveform(
    audio_data,
    num_frames: int = 2000,
) -> WaveformData:
    """将音频降采样到指定帧数，生成波形峰值数组。

    使用向量化操作替代 Python 循环，对大音频（5分钟+）友好。

    算法：
    1. 如果音频是多声道，混音为单声道
    2. 将音频划分为 num_frames 个等长段落
    3. 每段取绝对值的最大值作为该帧的峰值
    4. 归一化到 [0, 1]

    Args:
        audio_data: AudioData 对象（samples, sample_rate, duration）
        num_frames: 目标帧数，默认 2000

    Returns:
        WaveformData 对象

    示例（前端使用）：
        data = compute_waveform(audio, num_frames=2000)
        pps = canvas_width / data.duration  # 前端自己算
        for frame_idx, peak in enumerate(data.peaks):
            x = frame_idx / data.total_frames * canvas_width
            height = peak * canvas_height
            draw_rect(x, canvas_height - height, bar_width, height)
    """
    samples = np.asarray(audio_data.samples, dtype=np.float32)

    # 混音为单声道
    if samples.ndim == 2:
        samples = np.mean(samples, axis=0)

    duration = float(audio_data.duration)
    sr = int(audio_data.sample_rate)

    # 向量化：reshape + max（无 Python 循环）
    # 取 len(samples) 能被 num_frames 整除的前缀，忽略尾部余数
    total_samples = len(samples)
    n = total_samples // num_frames
    if n < 1:
        n = 1
        num_frames = total_samples

    # 截断到完整帧
    truncated = samples[:n * num_frames]
    # shape: (num_frames, n) 然后沿 axis=1 取 max
    peaks = np.max(np.abs(truncated.reshape(num_frames, n)), axis=1).astype(np.float32)

    # 归一化到 [0, 1]
    max_val = np.max(peaks)
    if max_val > 0:
        peaks = peaks / max_val

    frame_interval = duration / num_frames

    return WaveformData(
        peaks=peaks,
        duration=duration,
        sample_rate=sr,
        frame_interval=frame_interval,
    )