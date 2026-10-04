"""BTC-SL (Bi-directional Transformer for Chords, Self-Label) 插件。

基于 ChordMini 项目 (https://github.com/ptnghia-j/ChordMini) 的 BTC-SL 模型，
使用 170 类大词汇表进行和弦识别。支持高级推理流水线（滑动窗口 + 重叠投票 + 时序平滑）。
"""

from __future__ import annotations

import os
import logging
from typing import Any

import numpy as np
import torch

from src.plugins import BasePlugin
from src.kernel.core.resource_controller import ResourceController

logger = logging.getLogger(__name__)

# ---------- ChordMini 子模块资源路径 ----------
_EXTERNAL_DIR = os.path.join(os.path.dirname(__file__), "external", "chordmini")
_CHORDMINI_SRC = os.path.join(_EXTERNAL_DIR, "src")

_CHECKPOINTS_DIR = os.path.join(_EXTERNAL_DIR, "checkpoints")


def _setup_chordmini_imports():
    """加载独立命名空间中的 BTC 推理代码，保留主程序的包路径。"""
    from src.plugins.chord.chordmini_runtime import load_btc_runtime

    return load_btc_runtime(_CHORDMINI_SRC)


# 延迟导入，避免在模块加载时就触发子模块依赖
_BTC_model_cls = None
_ModelConfig_cls = None
_predict_sliding_windows = None
_extract_state_dict_and_stats = None


def _ensure_imports():
    global _BTC_model_cls, _ModelConfig_cls
    global _predict_sliding_windows, _extract_state_dict_and_stats
    if all(value is not None for value in (
        _BTC_model_cls, _ModelConfig_cls, _predict_sliding_windows, _extract_state_dict_and_stats,
    )):
        return
    runtime = _setup_chordmini_imports()
    from src.utils.checkpoint_utils import extract_state_dict_and_stats

    _BTC_model_cls = runtime.model_class
    _ModelConfig_cls = runtime.config_class
    _predict_sliding_windows = runtime.predict_sliding_windows
    _extract_state_dict_and_stats = extract_state_dict_and_stats


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

    if sr != 22050:
        audio = librosa.resample(audio.astype(np.float64), orig_sr=sr, target_sr=22050).astype(np.float32)
        sr = 22050

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

    支持高级推理流水线：滑动窗口 + 重叠投票 + 时序平滑。
    默认加载原始 Teacher ``btc_model_large_voca.pt``，缺失时选择 CL 训练权重。
    """

    # 默认 checkpoint 优先级：原始 Teacher（已验证可用）> CL 训练版（epoch 2 欠训练，待更多轮次后启用）
    _DEFAULT_CHECKPOINT_CANDIDATES = [
        os.path.join(_CHECKPOINTS_DIR, "btc_model_large_voca.pt"),
        os.path.join(_CHECKPOINTS_DIR, "btc_model_best.pth"),
    ]

    @property
    def name(self) -> str:
        return "chord_btc_sl"

    @property
    def version(self) -> str:
        return "2.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> dict[str, Any]:
        stem_name = kwargs.get("stem_name", "piano")
        from src.audio.contracts import as_audio, to_mono

        if hasattr(rc, "get_audio_buffer"):
            audio, sr = rc.get_audio_buffer(stem_name)
        else:
            audio = rc.get_buffer(stem_name)
            sr = rc.get_metadata("sample_rate") or 22050
        audio = to_mono(as_audio(audio, int(sr)))

        # 1. 检查源码和权重、加载模型；缺失资源时在提取特征之前失败。
        checkpoint_path = kwargs.get("checkpoint")
        model, mean, std = self._init_model(rc, checkpoint_path=checkpoint_path)

        # 2. CQT 特征提取（重采样到 22050，不归一化，由推理流水线内部处理）
        features = _extract_cqt_features(audio, sr)
        # CQT 使用 22050 Hz；修正 sr 以保证 _run_length_encode 时间戳正确
        cqt_sr = 22050

        # 3. 高级推理流水线（滑动窗口 + 重叠投票 + 时序平滑）
        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        predictions = self._run_inference(
            model, features, device, mean, std,
            model_type="BTC",
            seq_len=kwargs.get("seq_len", 108),
            batch_size=kwargs.get("batch_size", 32),
            use_overlap=kwargs.get("use_overlap", True),
            overlap_ratio=kwargs.get("overlap_ratio", 0.5),
            smooth_predictions=kwargs.get("smooth_predictions", True),
            kernel_size=kwargs.get("smooth_kernel", 9),
            use_gaussian=kwargs.get("use_gaussian", True),
        )

        # 4. 帧级预测 → 和弦段
        chords = _run_length_encode(predictions, hop_length=2048, sr=cqt_sr)

        rc.set_metadata(f"chord_raw_{stem_name}", chords)
        return {"status": "success", "stem": stem_name, "data": chords}

    def _init_model(self, rc: ResourceController, checkpoint_path: str | None = None):
        """加载或复用 BTC 模型。"""
        _ensure_imports()

        # 确定 checkpoint 路径
        if checkpoint_path is None:
            checkpoint_path = self._resolve_checkpoint_path()

        cache_key = f"btc_sl_model:{checkpoint_path}"
        cached = rc.get_metadata(cache_key) if hasattr(rc, "get_metadata") else None
        if cached is not None:
            return cached

        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        state_dict, mean, std = _extract_state_dict_and_stats(checkpoint)

        config = _ModelConfig_cls()
        model = _BTC_model_cls(config=config)
        model.load_state_dict(state_dict, strict=True)
        model.eval()

        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        model = model.to(device)

        result = (model, mean, std)
        rc.set_metadata(cache_key, result)
        logger.info("BTC-SL loaded %s on %s (%d tensors)",
                    os.path.basename(checkpoint_path), device, len(state_dict))
        return result

    @classmethod
    def _resolve_checkpoint_path(cls) -> str:
        for path in cls._DEFAULT_CHECKPOINT_CANDIDATES:
            path = os.path.normpath(path)
            if os.path.isfile(path):
                return path
        raise FileNotFoundError(
            f"BTC-SL 权重文件不存在，已检查: {cls._DEFAULT_CHECKPOINT_CANDIDATES}"
        )

    @staticmethod
    def _run_inference(
        model,
        features: np.ndarray,
        device,
        mean: float,
        std: float,
        model_type: str = "BTC",
        seq_len: int = 108,
        batch_size: int = 32,
        use_overlap: bool = True,
        overlap_ratio: float = 0.5,
        smooth_predictions: bool = True,
        kernel_size: int = 9,
        use_gaussian: bool = True,
    ) -> np.ndarray:
        """使用 ChordMini 的高级推理流水线：滑动窗口 + 重叠投票 + 时序平滑。"""
        predictions = _predict_sliding_windows(
            model=model,
            feature_matrix=features,
            mean=mean,
            std=std,
            seq_len=seq_len,
            batch_size=batch_size,
            model_type=model_type,
            n_classes=170,
            vote_aggregation="logit",
            use_overlap=use_overlap,
            overlap_ratio=overlap_ratio,
            smooth_logits=(model_type == "ChordNet" or model_type == "BTC"),
            smooth_predictions=smooth_predictions,
            kernel_size=kernel_size,
            use_gaussian=use_gaussian,
        )
        return np.asarray(predictions, dtype=np.int64)
