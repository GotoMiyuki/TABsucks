"""BTC-SL (Bi-directional Transformer for Chords, Self-Label) 插件。

基于 ChordMini 项目 (https://github.com/ptnghia-j/ChordMini) 的 BTC-SL 模型，
使用 170 类大词汇表进行和弦识别。
"""

from __future__ import annotations

import os
import sys
from typing import Any

import numpy as np
import torch

from src.plugins import BasePlugin
from src.kernel.core.resource_controller import ResourceController

# ---------- 路径设置：将 ChordMini 子模块加入 sys.path ----------
_EXTERNAL_DIR = os.path.join(os.path.dirname(__file__), "external", "chordmini")
if _EXTERNAL_DIR not in sys.path:
    sys.path.insert(0, _EXTERNAL_DIR)

# 延迟导入，避免在模块加载时就触发子模块依赖
_BTC_model_cls = None
_ModelConfig_cls = None


def _ensure_imports():
    global _BTC_model_cls, _ModelConfig_cls
    if _BTC_model_cls is None:
        from src.models.btc_model import BTC_model
        from src.models.common.config import ModelConfig
        _BTC_model_cls = BTC_model
        _ModelConfig_cls = ModelConfig


# ---------- 170 类词汇表 ----------
_ROOT_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_QUALITY_LIST = [
    "min", "maj", "dim", "aug", "min6", "maj6", "min7", "minmaj7",
    "maj7", "7", "dim7", "hdim7", "sus2", "sus4",
]


def idx2voca_chord(idx: int) -> str:
    """将 170 类索引映射为和弦标签。

    0-167: 12 root x 14 quality, 168: X, 169: N
    """
    if idx == 169:
        return "N"
    if idx == 168:
        return "X"
    if 0 <= idx < 168:
        root_idx = idx // 14
        quality_idx = idx % 14
        root = _ROOT_NOTES[root_idx]
        quality = _QUALITY_LIST[quality_idx]
        return root if quality == "maj" else f"{root}:{quality}"
    return "X"


# ---------- CQT 特征提取 ----------
def _extract_cqt_features(audio: np.ndarray, sr: int) -> np.ndarray:
    """提取对数幅度 CQT 特征。

    Returns:
        shape (num_frames, 144) 的特征矩阵。
    """
    import librosa

    cqt = librosa.cqt(
        audio,
        sr=sr,
        n_bins=144,
        bins_per_octave=24,
        hop_length=2048,
        fmin=librosa.note_to_hz("C1"),
    )
    return np.log(np.abs(cqt) + 1e-6).T


# ---------- 游程编码 ----------
def _run_length_encode(predictions: np.ndarray, hop_length: int, sr: int) -> list[dict]:
    """将帧级预测转为带时间戳的和弦段列表（游程编码）。"""
    frame_duration = hop_length / sr
    chords: list[dict] = []
    if len(predictions) == 0:
        return chords

    current_idx = int(predictions[0])
    start_frame = 0

    for i in range(1, len(predictions)):
        pred = int(predictions[i])
        if pred != current_idx:
            chords.append({
                "start": start_frame * frame_duration,
                "end": i * frame_duration,
                "chord": idx2voca_chord(current_idx),
            })
            current_idx = pred
            start_frame = i

    # 最后一段
    chords.append({
        "start": start_frame * frame_duration,
        "end": len(predictions) * frame_duration,
        "chord": idx2voca_chord(current_idx),
    })
    return chords


class BTCSLChordPlugin(BasePlugin):
    """BTC-SL 和弦识别插件。

    使用 ChordMini 的 BTC 模型（170 类大词汇表），从 ResourceController
    获取音频 buffer 进行推理，输出 ``{"start", "end", "chord"}`` 格式。
    """

    @property
    def name(self) -> str:
        return "chord_btc_sl"

    @property
    def version(self) -> str:
        return "1.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> dict[str, Any]:
        stem_name = kwargs.get("stem_name", "piano")
        audio = rc.get_buffer(stem_name)
        sr = rc.get_metadata("sample_rate") or 22050

        # 1. CQT 特征提取
        features = _extract_cqt_features(audio, sr)

        # 2. 归一化
        model, mean, std = self._init_model(rc)
        features = (features - mean) / std

        # 3. 推理
        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        predictions = self._run_inference(model, features, device)

        # 4. 后处理
        chords = _run_length_encode(predictions, hop_length=2048, sr=sr)

        rc.set_metadata(f"chord_raw_{stem_name}", chords)
        return {"status": "success", "stem": stem_name, "data": chords}

    def _init_model(self, rc: ResourceController):
        """加载或复用 BTC-SL 模型。"""
        _ensure_imports()

        pretrained_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "pretrained", "btc_model_large_voca.pt"
        )
        pretrained_path = os.path.normpath(pretrained_path)

        if not os.path.exists(pretrained_path):
            raise FileNotFoundError(
                f"BTC-SL 权重文件不存在: {pretrained_path}，"
                f"请从 https://github.com/jayg996/BTC-ISMIR19 下载 btc_model_large_voca.pt"
            )

        # 尝试从 RC 缓存获取已加载的模型
        cached = rc.get_metadata("btc_sl_model") if hasattr(rc, "get_metadata") else None
        if cached is not None:
            return cached

        checkpoint = torch.load(pretrained_path, map_location="cpu", weights_only=False)

        # 提取 state_dict（兼容多种 checkpoint 格式）
        state_dict = checkpoint.get("model_state_dict", checkpoint.get("model", checkpoint))
        if isinstance(state_dict, dict) and state_dict and next(iter(state_dict)).startswith("module."):
            state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}

        # 提取归一化统计量
        mean = float(checkpoint.get("mean", 0.0))
        std = float(checkpoint.get("std", 1.0))
        std = max(std, 1e-8)

        # 构建模型
        config = _ModelConfig_cls()
        model = _BTC_model_cls(config=config)
        model.load_state_dict(state_dict)
        model.eval()

        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        model = model.to(device)

        result = (model, mean, std)
        rc.set_metadata("btc_sl_model", result)
        return result

    @staticmethod
    def _run_inference(model, features: np.ndarray, device) -> np.ndarray:
        """逐 chunk 推理，返回帧级预测索引。"""
        tensor = torch.tensor(features, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            predictions = model.predict(tensor, per_frame=True, smooth=False)
        return predictions.squeeze(0).cpu().numpy()
