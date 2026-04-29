"""音频加载模块，支持多格式输入。

支持的格式：MP3, WAV, FLAC, M4A
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import librosa
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence


class AudioFormat(Enum):
    """支持的音频格式。"""

    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    M4A = "m4a"


@dataclass(frozen=True)
class AudioData:
    """不可变的音频数据结构。

    Attributes:
        samples: 单声道音频样本，形状为 (n_samples,)
        sample_rate: 采样率（Hz）
        duration: 时长（秒）
    """

    samples: np.ndarray
    sample_rate: int
    duration: float

    @property
    def channels(self) -> int:
        """音频通道数。"""
        return self.samples.ndim

    @property
    def n_samples(self) -> int:
        """样本数量。"""
        return self.samples.shape[-1]


class AudioLoaderError(Exception):
    """音频加载失败时抛出。"""

    pass


class IAudioLoader(Protocol):
    """音频加载器接口，定义统一的加载行为。"""

    def load(self, path: str | Path) -> AudioData:
        """加载音频文件。"""
        ...


def _validate_path(path: str | Path) -> Path:
    """验证路径是否存在且可读。"""
    p = Path(path)
    if not p.exists():
        raise AudioLoaderError(f"文件不存在: {p}")
    if not p.is_file():
        raise AudioLoaderError(f"不是有效文件: {p}")
    return p


def _validate_format(path: Path) -> None:
    """验证文件格式是否支持。"""
    suffix = path.suffix.lower().lstrip(".")
    supported = {fmt.value for fmt in AudioFormat}
    if suffix not in supported:
        raise AudioLoaderError(
            f"不支持的格式: .{suffix}，支持: {', '.join(sorted(supported))}"
        )


def load_audio(path: str | Path, sr: int = 44100) -> AudioData:
    """加载音频文件并转为单声道 numpy 数组。

    Args:
        path: 音频文件路径，支持相对路径和绝对路径。
        sr: 目标采样率（Hz），默认 44100Hz。
            设为 None 则保留原始采样率。

    Returns:
        AudioData 对象，包含音频样本、采样率和时长。

    Raises:
        AudioLoaderError: 文件不存在、不支持格式或加载失败。

    Examples:
        >>> data = load_audio("test.wav", sr=22050)
        >>> print(data.duration)
        3.45
    """
    path = _validate_path(path)
    _validate_format(path)

    try:
        y, loaded_sr = librosa.load(path, sr=sr, mono=True)
        duration = len(y) / loaded_sr
        return AudioData(samples=y, sample_rate=loaded_sr, duration=duration)
    except Exception as e:
        raise AudioLoaderError(f"加载失败: {e}") from e


def load_audio_multi_channel(path: str | Path) -> AudioData:
    """加载音频文件并保留多通道原始采样率。

    Args:
        path: 音频文件路径。

    Returns:
        AudioData 对象，samples 形状为 (channels, n_samples)。

    Raises:
        AudioLoaderError: 文件不存在或加载失败。
    """
    path = _validate_path(path)
    _validate_format(path)

    try:
        y, sr = librosa.load(path, sr=None, mono=False)
        if y.ndim == 1:
            y = y[np.newaxis, :]
        duration = y.shape[-1] / sr
        return AudioData(samples=y, sample_rate=sr, duration=duration)
    except Exception as e:
        raise AudioLoaderError(f"加载失败: {e}") from e


def save_audio(path: str | Path, data: AudioData) -> None:
    """将 AudioData 保存为音频文件。

    Args:
        path: 输出路径（扩展名决定格式）
        data: AudioData 对象

    Raises:
        AudioLoaderError: 保存失败
    """
    import soundfile as sf

    path = Path(path)
    try:
        sf.write(path, data.samples.T, data.sample_rate)
    except Exception as e:
        raise AudioLoaderError(f"保存失败: {e}") from e


def get_api_key() -> str:
    """从环境变量读取 API Key。

    Returns:
        API Key 字符串。

    Raises:
        RuntimeError: 环境变量未设置。
    """
    key = os.environ.get("AUDIO_API_KEY")
    if not key:
        raise RuntimeError("AUDIO_API_KEY 环境变量未设置")
    return key
