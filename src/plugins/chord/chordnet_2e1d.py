"""ChordNet (2E1D) 和弦识别插件。

基于 ChordMini 项目 (https://github.com/ptnghia-j/ChordMini) 的 ChordNet 模型，
使用频率轴+时间轴双编码器 + 交叉注意力解码器架构，170 类大词汇表。
"""

from __future__ import annotations

import os
import sys
from typing import Any

import numpy as np
import torch

from src.plugins import BasePlugin
from src.kernel.core.resource_controller import ResourceController

# ---------- 路径设置：复用 btc_sl 的 ChordMini 子模块路径 ----------
_EXTERNAL_DIR = os.path.join(os.path.dirname(__file__), "external", "chordmini")
_CHORDMINI_SRC = os.path.join(_EXTERNAL_DIR, "src")
if _CHORDMINI_SRC not in sys.path:
    sys.path.insert(0, _CHORDMINI_SRC)

_CHECKPOINTS_DIR = os.path.join(_EXTERNAL_DIR, "checkpoints")


def _setup_chordmini_imports():
    """将 ChordMini 的 src 目录注入 TABsucks 的 src 包路径。"""
    import src as _tabsucks_src
    if _CHORDMINI_SRC not in _tabsucks_src.__path__:
        _tabsucks_src.__path__.insert(0, _CHORDMINI_SRC)


# 延迟导入
_ChordNet_cls = None
_get_chordnet_config = None
_predict_sliding_windows = None
_extract_state_dict_and_stats = None


def _ensure_imports():
    global _ChordNet_cls, _get_chordnet_config
    global _predict_sliding_windows, _extract_state_dict_and_stats
    _setup_chordmini_imports()
    if _ChordNet_cls is None:
        from src.models.chord_net import ChordNet
        _ChordNet_cls = ChordNet
    if _get_chordnet_config is None:
        from src.models.common.config import get_chordnet_config
        _get_chordnet_config = get_chordnet_config
    if _predict_sliding_windows is None:
        import importlib.util
        _inf_path = os.path.join(_CHORDMINI_SRC, "evaluation", "utils", "inference.py")
        _mod_name = f"_chordmini_inference_{id(_inf_path)}"
        spec = importlib.util.spec_from_file_location(_mod_name, _inf_path)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        _predict_sliding_windows = _mod.predict_sliding_windows
    if _extract_state_dict_and_stats is None:
        from src.utils.checkpoint_utils import extract_state_dict_and_stats
        _extract_state_dict_and_stats = extract_state_dict_and_stats


# 复用 btc_sl 的 CQT 提取和游程编码
from src.plugins.chord.btc_sl import _extract_cqt_features, _run_length_encode


class ChordNet2E1DPlugin(BasePlugin):
    """ChordNet (2E1D) 和弦识别插件。

    使用 ChordMini 的 ChordNet 模型（频率轴+时间轴双编码器），170 类词汇表。
    支持高级推理流水线：滑动窗口 + 重叠投票 + 时序平滑。
    默认加载 ChordMini CL 训练的 ``2e1d_model_best.pth``。
    """

    _DEFAULT_CHECKPOINT = os.path.join(_CHECKPOINTS_DIR, "2e1d_model_best.pth")

    @property
    def name(self) -> str:
        return "chord_chordnet_2e1d"

    @property
    def version(self) -> str:
        return "1.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> dict[str, Any]:
        stem_name = kwargs.get("stem_name", "piano")
        audio = rc.get_buffer(stem_name)
        sr = rc.get_metadata("sample_rate") or 22050

        # 1. CQT 特征提取（不归一化，由推理流水线内部处理）
        features = _extract_cqt_features(audio, sr)

        # 2. 加载模型
        checkpoint_path = kwargs.get("checkpoint")
        model, mean, std = self._init_model(rc, checkpoint_path=checkpoint_path)

        # 3. 高级推理流水线
        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        predictions = self._run_inference(
            model, features, device, mean, std,
            seq_len=kwargs.get("seq_len", 108),
            batch_size=kwargs.get("batch_size", 32),
            use_overlap=kwargs.get("use_overlap", True),
            overlap_ratio=kwargs.get("overlap_ratio", 0.5),
            smooth_predictions=kwargs.get("smooth_predictions", True),
            kernel_size=kwargs.get("smooth_kernel", 9),
            use_gaussian=kwargs.get("use_gaussian", True),
        )

        # 4. 帧级预测 → 和弦段
        chords = _run_length_encode(predictions, hop_length=2048, sr=sr)

        rc.set_metadata(f"chord_raw_{stem_name}", chords)
        return {"status": "success", "stem": stem_name, "data": chords}

    def _init_model(self, rc: ResourceController, checkpoint_path: str | None = None):
        """加载或复用 ChordNet 模型。"""
        _ensure_imports()

        if checkpoint_path is None:
            checkpoint_path = self._resolve_checkpoint_path()

        cache_key = f"chordnet_2e1d:{checkpoint_path}"
        cached = rc.get_metadata(cache_key) if hasattr(rc, "get_metadata") else None
        if cached is not None:
            return cached

        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        state_dict, mean, std = _extract_state_dict_and_stats(checkpoint)

        # 使用默认 ChordNet config，strict=False 处理架构参数差异
        config = _get_chordnet_config()
        model = _ChordNet_cls(**config.to_chordnet_kwargs())
        model.load_state_dict(state_dict, strict=False)
        model.eval()

        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        model = model.to(device)

        result = (model, mean, std)
        rc.set_metadata(cache_key, result)
        return result

    @classmethod
    def _resolve_checkpoint_path(cls) -> str:
        path = os.path.normpath(cls._DEFAULT_CHECKPOINT)
        if os.path.exists(path):
            return path
        raise FileNotFoundError(
            f"ChordNet 2E1D 权重文件不存在: {path}，"
            f"请确保 ChordMini 子模块的 checkpoints/ 目录包含 2e1d_model_best.pth"
        )

    @staticmethod
    def _run_inference(
        model,
        features: np.ndarray,
        device,
        mean: float,
        std: float,
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
            model_type="ChordNet",
            n_classes=170,
            vote_aggregation="logit",
            use_overlap=use_overlap,
            overlap_ratio=overlap_ratio,
            smooth_logits=True,
            smooth_predictions=smooth_predictions,
            kernel_size=kernel_size,
            use_gaussian=use_gaussian,
            use_chordnet_defaults=True,
        )
        return np.asarray(predictions, dtype=np.int64)
