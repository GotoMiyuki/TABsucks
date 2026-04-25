"""音轨分离模块，使用 BS-RoFormer 模型将音频分离为多轨。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.audio.loader import AudioData

# 预训练模型列表（待接入）
SUPPORTED_MODELS = [
    "bs_roformer_ep_300_sdr_9",
    "bs_roformer_ep_398",
    "htdemucs_ft",
    "htdemucs",
]


class TrackId(Enum):
    """分离音轨类型。"""

    VOCALS = "vocals"
    DRUMS = "drums"
    BASS = "bass"
    PIANO = "piano"
    GUITAR = "guitar"
    OTHER = "other"


TRACK_LABELS: dict[TrackId, str] = {
    TrackId.VOCALS: "人声",
    TrackId.DRUMS: "鼓",
    TrackId.BASS: "贝斯",
    TrackId.PIANO: "钢琴",
    TrackId.GUITAR: "吉他",
    TrackId.OTHER: "其他",
}


@dataclass(frozen=True)
class SeparationResult:
    """音轨分离结果，所有轨道均为不可变 numpy 数组。"""

    vocals: np.ndarray
    drums: np.ndarray
    bass: np.ndarray
    piano: np.ndarray
    guitar: np.ndarray
    other: np.ndarray
    sample_rate: int

    def get_track(self, track_id: TrackId) -> np.ndarray:
        """根据 TrackId 获取对应音轨。

        Args:
            track_id: 音轨类型。

        Returns:
            对应音轨的 numpy 数组，形状为 (n_samples,)。
        """
        return getattr(self, track_id.value)


class SeparatorError(Exception):
    """分离操作失败时抛出。"""

    pass


class Separator:
    """音频分离器，基于 BS-RoFormer 模型。"""

    def __init__(self, model_name: str = "bs_roformer_ep_300_sdr_9") -> None:
        """初始化分离器。

        Args:
            model_name: 预训练模型名称，默认使用 BS-RoFormer。
        """
        if model_name not in SUPPORTED_MODELS:
            raise SeparatorError(
                f"不支持的模型: {model_name}，支持: {SUPPORTED_MODELS}"
            )
        self.model_name = model_name
        self._model = None  # 延迟加载，实际使用时才加载模型

    def separate(self, audio: AudioData) -> SeparationResult:
        """对音频进行音轨分离。

        Args:
            audio: 加载后的音频数据。

        Returns:
            SeparationResult，包含 6 轨分离结果。

        Raises:
            SeparatorError: 分离过程出错。

        Examples:
            >>> from src.audio.loader import load_audio
            >>> from src.separation import Separator
            >>> audio = load_audio("song.mp3")
            >>> sep = Separator()
            >>> result = sep.separate(audio)
            >>> print(result.vocals.shape)
        """
        # TODO: 实际接入 BS-RoFormer 模型推理
        # 目前返回占位数据
        n_samples = audio.samples.shape[-1]
        sr = audio.sample_rate

        noise_level = 0.001
        placeholder = audio.samples * 0.0  # 全零占位，实际接入后替换

        return SeparationResult(
            vocals=placeholder + np.random.randn(n_samples) * noise_level,
            drums=placeholder + np.random.randn(n_samples) * noise_level,
            bass=placeholder + np.random.randn(n_samples) * noise_level,
            piano=placeholder + np.random.randn(n_samples) * noise_level,
            guitar=placeholder + np.random.randn(n_samples) * noise_level,
            other=placeholder + np.random.randn(n_samples) * noise_level,
            sample_rate=sr,
        )

    def separate_file(self, path: str | Path) -> SeparationResult:
        """从文件路径直接进行分离。

        Args:
            path: 音频文件路径。

        Returns:
            SeparationResult。
        """
        from src.audio.loader import load_audio

        audio = load_audio(path)
        return self.separate(audio)
