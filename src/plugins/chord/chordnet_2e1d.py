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


class _ProgressReportingModel(torch.nn.Module):
    """Delegate model calls while reporting completed inference batches."""

    def __init__(self, model, total_batches: int, progress_callback) -> None:
        super().__init__()
        self.module = model
        self._total_batches = max(1, int(total_batches))
        self._completed_batches = 0
        self._progress_callback = progress_callback

    def forward(self, *args, **kwargs):
        result = self.module(*args, **kwargs)
        self._completed_batches += 1
        self._progress_callback(
            min(1.0, self._completed_batches / self._total_batches)
        )
        return result


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
    reports_progress = True

    @property
    def name(self) -> str:
        return "chord_chordnet_2e1d"

    @property
    def version(self) -> str:
        return "1.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> dict[str, Any]:
        stem_name = kwargs.get("stem_name", "piano")
        progress_callback = kwargs.get("progress_callback")

        def report(progress: float) -> None:
            if progress_callback is not None:
                progress_callback(progress)

        report(0.02)
        audio = rc.get_buffer(stem_name)
        sr = rc.get_metadata("sample_rate") or 22050

        # 1. CQT 特征提取（重采样到 22050，不归一化，由推理流水线内部处理）
        report(0.05)
        features = _extract_cqt_features(audio, sr)
        report(0.25)
        cqt_sr = 22050

        # 2. 加载模型
        checkpoint_path = kwargs.get("checkpoint")
        report(0.28)
        model, mean, std = self._init_model(rc, checkpoint_path=checkpoint_path)
        report(0.35)

        # 3. 高级推理流水线
        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"

        def report_inference(progress: float) -> None:
            bounded = max(0.0, min(float(progress), 1.0))
            report(0.35 + 0.60 * bounded)

        predictions = self._run_inference(
            model, features, device, mean, std,
            seq_len=kwargs.get("seq_len", 108),
            batch_size=kwargs.get("batch_size", 32),
            use_overlap=kwargs.get("use_overlap", True),
            overlap_ratio=kwargs.get("overlap_ratio", 0.5),
            smooth_predictions=kwargs.get("smooth_predictions", True),
            kernel_size=kwargs.get("smooth_kernel", 9),
            use_gaussian=kwargs.get("use_gaussian", True),
            progress_callback=report_inference,
        )

        # 4. 帧级预测 → 和弦段
        report(0.97)
        chords = _run_length_encode(predictions, hop_length=2048, sr=cqt_sr)

        rc.set_metadata(f"chord_raw_{stem_name}", chords)
        report(0.99)
        return {"status": "success", "stem": stem_name, "data": chords}

    def _init_model(self, rc: ResourceController, checkpoint_path: str | None = None):
        """加载或复用 ChordNet 模型。从 checkpoint state_dict 推断架构参数。"""
        _ensure_imports()

        if checkpoint_path is None:
            checkpoint_path = self._resolve_checkpoint_path()

        cache_key = f"chordnet_2e1d:{checkpoint_path}"
        cached = rc.get_metadata(cache_key) if hasattr(rc, "get_metadata") else None
        if cached is not None:
            return cached

        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        state_dict, mean, std = _extract_state_dict_and_stats(checkpoint)

        # 从 checkpoint state_dict 推断实际架构参数
        arch = self._infer_architecture(state_dict)
        model = _ChordNet_cls(**arch)
        model.load_state_dict(state_dict, strict=False)
        model.eval()

        device = rc.get_current_device() if hasattr(rc, "get_current_device") else "cpu"
        model = model.to(device)

        result = (model, mean, std)
        rc.set_metadata(cache_key, result)
        return result

    @staticmethod
    def _infer_architecture(state_dict: dict) -> dict:
        """从 checkpoint state_dict 推断 ChordNet 架构参数。

        不同 checkpoint 可能使用不同的 n_group / 层数 / 头数。
        通过检查关键参数的 shape 来还原实际架构。
        """
        # n_freq / n_classes: 从 fc 层权重推断
        n_freq = 144
        n_classes = 170
        if "fc.weight" in state_dict:
            n_classes = state_dict["fc.weight"].shape[0]
            n_freq = state_dict["fc.weight"].shape[1]

        # n_group: 从 encoder_f 第一个 attention 层的 out_proj 推断
        # out_proj.weight shape = (d_model, d_model), d_model = n_freq // n_group
        n_group = 2  # 常见默认值
        for key, value in state_dict.items():
            if "encoder_f.0.attn_layer.0.out_proj.weight" in key:
                d_model = value.shape[0]
                if d_model > 0 and n_freq % d_model == 0:
                    n_group = n_freq // d_model
                break

        # f_layer / t_layer / d_layer: 通过统计 encoder_f / encoder_t / decoder 中
        # attn_layer 的数量来推断层数
        def _count_layers(prefix: str) -> int:
            indices = set()
            for key in state_dict:
                if prefix not in key:
                    continue
                parts = key.split(".")
                for i, part in enumerate(parts[:-1]):
                    if part.startswith("attn_layer") and i + 1 < len(parts):
                        try:
                            indices.add(int(parts[i + 1]))
                        except ValueError:
                            pass
            return max(indices) + 1 if indices else None

        f_layer = _count_layers("encoder_f.0.attn_layer") or 3
        t_layer = _count_layers("encoder_t.0.attn_layer") or 4
        d_layer = _count_layers("decoder.attn_layer1") or 3

        # f_head: 从 encoder_f 第一个 attention 层的 in_proj_weight 推断
        # in_proj_weight shape = (3 * d_model, d_model), qkv 各占 d_model
        # head_dim = d_model / f_head, 但这里 d_model 必须能被 f_head 整除
        # 简化：用 d_model 的约数作为候选
        def _infer_heads(prefix: str, d_model: int) -> int:
            for key in state_dict:
                if prefix in key and "in_proj_weight" in key:
                    embed_dim_total = state_dict[key].shape[1]  # d_model
                    if embed_dim_total == d_model:
                        # 查找 d_model 的因子作为可能的 head 数
                        for h in (8, 4, 2, 1):
                            if d_model % h == 0:
                                return h
                    break
            return 2

        d_model = n_freq // n_group
        f_head = _infer_heads("encoder_f.0.attn_layer.0", d_model)
        t_head = _infer_heads("encoder_t.0.attn_layer.0", n_freq)
        d_head = _infer_heads("decoder.attn_layer1.0", n_freq)

        # dropout: 无法从 state_dict 推断，使用合理默认值
        return {
            "n_freq": n_freq,
            "n_classes": n_classes,
            "n_group": n_group,
            "f_layer": f_layer,
            "f_head": f_head,
            "t_layer": t_layer,
            "t_head": t_head,
            "d_layer": d_layer,
            "d_head": d_head,
            "dropout": 0.3,
        }

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
        progress_callback=None,
    ) -> np.ndarray:
        """使用 ChordMini 的高级推理流水线：滑动窗口 + 重叠投票 + 时序平滑。"""
        inference_model = model
        if progress_callback is not None and features.size > 0:
            original_frames = int(features.shape[0])
            seq_len = max(1, int(seq_len))
            remainder = original_frames % seq_len
            padded_frames = original_frames + (
                0 if remainder == 0 else seq_len - remainder
            )
            effective_overlap = (
                max(0.0, min(float(overlap_ratio), 0.99))
                if use_overlap
                else 0.0
            )
            stride = (
                max(1, int(seq_len * (1.0 - effective_overlap)))
                if effective_overlap > 0
                else seq_len
            )
            window_count = max(
                1,
                ((padded_frames - seq_len) // stride) + 1,
            )
            total_batches = max(
                1,
                (window_count + batch_size - 1) // batch_size,
            )
            inference_model = _ProgressReportingModel(
                model,
                total_batches,
                progress_callback,
            )

        predictions = _predict_sliding_windows(
            model=inference_model,
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
