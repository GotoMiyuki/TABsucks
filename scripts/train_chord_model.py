"""Stage 1 伪标签训练包装脚本。

使用 ChordMini 的伪标签训练流水线，在无标注音频上训练和弦识别模型。
Teacher 模型 (BTC) 在线生成伪标签，Student 模型学习这些标签。

Usage:
    py scripts/train_chord_model.py --audio_dir data/fma_audio --model_type ChordNet
    py scripts/train_chord_model.py --audio_dir data/fma_audio --model_type BTC --use_kd
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── 路径设置 ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHORDMINI_ROOT = PROJECT_ROOT / "src" / "plugins" / "chord" / "external" / "chordmini"
CHORDMINI_SRC = CHORDMINI_ROOT / "src"

# 将 ChordMini 的 src 注入 TABsucks 的 src 包路径
sys.path.insert(0, str(CHORDMINI_SRC))
import src as _tabsucks_src
if str(CHORDMINI_SRC) not in _tabsucks_src.__path__:
    _tabsucks_src.__path__.insert(0, str(CHORDMINI_SRC))

# ── ChordMini 导入 ───────────────────────────────────────────────
from src.data import UnlabeledAudioDataset
from src.models.common.config import get_btc_config, get_chordnet_config
from src.models import create_btc_model, create_chordnet_model
from src.training.pseudo_labeling_trainer import PseudoLabelingTrainer
from src.utils import (
    extract_state_dict_and_stats,
    get_device,
    idx2voca_chord,
    load_checkpoint,
    save_checkpoint,
    set_random_seed,
)
from src.utils.dataloader import build_dataloader_kwargs

import numpy as np
import torch

DEFAULT_TEACHER = str(CHORDMINI_ROOT / "checkpoints" / "btc_model_large_voca.pt")
DEFAULT_SAVE_DIR = str(PROJECT_ROOT / "pretrained" / "chordmini_trained")


def parse_args():
    p = argparse.ArgumentParser(description="ChordMini Stage 1: Pseudo-Label Training")

    # 输入
    p.add_argument("--audio_dir", required=True, help="无标注音频目录")
    p.add_argument("--max_files", type=int, default=None, help="限制音频文件数量（调试用）")

    # 模型选择
    p.add_argument("--model_type", default="ChordNet", choices=["BTC", "ChordNet"])
    p.add_argument("--teacher_checkpoint", default=DEFAULT_TEACHER, help="Teacher checkpoint 路径")

    # 训练超参
    p.add_argument("--save_dir", default=DEFAULT_SAVE_DIR)
    p.add_argument("--batch_size", type=int, default=256)
    p.add_argument("--num_epochs", type=int, default=100)
    p.add_argument("--learning_rate", type=float, default=1e-4)
    p.add_argument("--weight_decay", type=float, default=1e-5)
    p.add_argument("--early_stopping_patience", type=int, default=10)
    p.add_argument("--seq_len", type=int, default=108)
    p.add_argument("--stride", type=int, default=108)

    # 学习率调度
    p.add_argument("--lr_schedule", default="cosine", choices=["cosine", "validation", "none"])
    p.add_argument("--use_warmup", action="store_true", default=True)
    p.add_argument("--warmup_epochs", type=int, default=10)
    p.add_argument("--warmup_start_lr", type=float, default=1e-4)
    p.add_argument("--warmup_end_lr", type=float, default=3e-4)
    p.add_argument("--min_learning_rate", type=float, default=1e-6)

    # 知识蒸馏
    p.add_argument("--use_kd", action="store_true", help="启用 KD loss")
    p.add_argument("--kd_alpha", type=float, default=0.5)
    p.add_argument("--temperature", type=float, default=3.0)

    # 损失函数
    p.add_argument("--use_focal_loss", action="store_true", default=True)
    p.add_argument("--focal_gamma", type=float, default=2.0)

    # 数据分割
    p.add_argument("--train_ratio", type=float, default=0.8)
    p.add_argument("--val_ratio", type=float, default=0.1)

    # 其他
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--num_workers", type=int, default=0)
    p.add_argument("--resume_checkpoint", default=None, help="从 checkpoint 恢复训练")

    # ChordNet 架构覆盖
    p.add_argument("--n_group", type=int, default=None)
    p.add_argument("--f_layer", type=int, default=None)
    p.add_argument("--f_head", type=int, default=None)
    p.add_argument("--t_layer", type=int, default=None)
    p.add_argument("--t_head", type=int, default=None)
    p.add_argument("--d_layer", type=int, default=None)
    p.add_argument("--d_head", type=int, default=None)
    p.add_argument("--dropout", type=float, default=None)

    return p.parse_args()


def build_model_config(args):
    if args.model_type == "BTC":
        return get_btc_config()
    config = get_chordnet_config()
    for attr in ("n_group", "f_layer", "f_head", "t_layer", "t_head", "d_layer", "d_head", "dropout"):
        val = getattr(args, attr, None)
        if val is not None:
            setattr(config, attr, val)
    config.seq_len = args.seq_len
    return config


def load_student(model_type, model_config, device, checkpoint_path=None):
    if model_type == "BTC":
        model = create_btc_model(model_config)
    else:
        model = create_chordnet_model(model_config)

    if checkpoint_path and os.path.exists(checkpoint_path):
        ckpt = load_checkpoint(checkpoint_path, device="cpu")
        if ckpt:
            sd, mean, std = extract_state_dict_and_stats(ckpt)
            model.load_state_dict(sd, strict=False)
            return model.to(device), mean, std
    return model.to(device), 0.0, 1.0


def load_teacher(checkpoint_path, seq_len, device):
    config = get_btc_config()
    config.seq_len = seq_len
    model = create_btc_model(config)
    ckpt = load_checkpoint(checkpoint_path, device="cpu")
    if ckpt:
        sd, _, _ = extract_state_dict_and_stats(ckpt)
        model.load_state_dict(sd, strict=False)
    model.eval()
    return model.to(device)


def main():
    args = parse_args()
    set_random_seed(args.seed, include_python_random=True)
    device = get_device()
    print(f"[train_chord_model] Device: {device}")
    print(f"[train_chord_model] Model: {args.model_type}")

    # 1. 构建模型配置
    model_config = build_model_config(args)
    model_config.seq_len = args.seq_len

    # 2. 加载 Student
    student, mean, std = load_student(args.model_type, model_config, device)
    print(f"[train_chord_model] Student loaded ({args.model_type})")

    # 3. 加载 Teacher (用于在线伪标签生成)
    teacher = load_teacher(args.teacher_checkpoint, args.seq_len, device)
    teacher_mean, teacher_std = 0.0, 1.0
    teacher_ckpt = load_checkpoint(args.teacher_checkpoint, device="cpu")
    if teacher_ckpt:
        _, teacher_mean, teacher_std = extract_state_dict_and_stats(teacher_ckpt)
    print(f"[train_chord_model] Teacher loaded from {args.teacher_checkpoint}")

    # 4. 构建词汇表
    idx_to_chord = idx2voca_chord()
    chord_to_idx = {v: k for k, v in idx_to_chord.items()}

    # 5. 加载无标注数据集
    print(f"[train_chord_model] Loading unlabeled audio from {args.audio_dir}")
    dataset = UnlabeledAudioDataset(
        audio_dir=args.audio_dir,
        config=model_config,
        seq_len=args.seq_len,
        stride=args.stride,
        max_files=args.max_files,
        random_seed=args.seed,
    )
    print(f"[train_chord_model] Dataset: {len(dataset)} segments")

    # 6. 归一化统计量
    normalization = {"mean": mean, "std": std}
    if mean == 0.0 and std == 1.0:
        print("[train_chord_model] Estimating normalization from dataset...")
        norm_params = dataset.get_normalization_params(num_samples=min(100, len(dataset)))
        normalization = {"mean": norm_params[0], "std": norm_params[1]}
        print(f"[train_chord_model] Normalization: mean={normalization['mean']:.4f}, std={normalization['std']:.4f}")

    # 7. 数据分割
    train_idx, val_idx, test_idx = dataset.split_indices(
        train_ratio=args.train_ratio, val_ratio=args.val_ratio, seed=args.seed
    )
    print(f"[train_chord_model] Split: train={len(train_idx)}, val={len(val_idx)}, test={len(test_idx)}")

    # 8. DataLoader
    dl_kwargs = build_dataloader_kwargs(device, args.num_workers)
    train_loader = torch.utils.data.DataLoader(
        dataset, batch_size=args.batch_size, sampler=torch.utils.data.SubsetRandomSampler(train_idx), **dl_kwargs
    )
    val_loader = torch.utils.data.DataLoader(
        dataset, batch_size=args.batch_size, sampler=torch.utils.data.SubsetRandomSampler(val_idx), **dl_kwargs
    ) if val_idx else None
    test_loader = torch.utils.data.DataLoader(
        dataset, batch_size=args.batch_size, sampler=torch.utils.data.SubsetRandomSampler(test_idx), **dl_kwargs
    ) if test_idx else None

    # 9. 优化器
    optimizer = torch.optim.AdamW(student.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)

    # 10. Trainer
    trainer = PseudoLabelingTrainer(
        model=student,
        optimizer=optimizer,
        teacher_model=teacher,
        teacher_mean=teacher_mean,
        teacher_std=teacher_std,
        device=device,
        num_epochs=args.num_epochs,
        checkpoint_dir=os.path.join(args.save_dir, args.model_type.lower()),
        normalization=normalization,
        idx_to_chord=idx_to_chord,
        use_kd_loss=args.use_kd,
        kd_alpha=args.kd_alpha,
        temperature=args.temperature,
        early_stopping_patience=args.early_stopping_patience,
        use_focal_loss=args.use_focal_loss,
        focal_gamma=args.focal_gamma,
        lr_schedule_type=args.lr_schedule,
        use_warmup=args.use_warmup,
        warmup_epochs=args.warmup_epochs,
        warmup_start_lr=args.warmup_start_lr,
        warmup_end_lr=args.warmup_end_lr,
        min_lr=args.min_learning_rate,
    )

    # 11. 恢复训练（可选）
    if args.resume_checkpoint:
        print(f"[train_chord_model] Resuming from {args.resume_checkpoint}")
        trainer.resume_from_checkpoint(args.resume_checkpoint)

    # 12. 训练
    print(f"[train_chord_model] Starting Stage 1 training for {args.num_epochs} epochs")
    trainer.train(train_loader, val_loader)

    # 13. 评估
    trainer.load_best_model()
    if test_loader:
        test_acc = trainer.evaluate_loader(test_loader)
        print(f"[train_chord_model] Test accuracy: {test_acc:.4f}")

    best_path = os.path.join(args.save_dir, args.model_type.lower(), "best_model.pth")
    print(f"\n{'='*60}")
    print(f"[train_chord_model] Training complete!")
    print(f"[train_chord_model] Best model: {best_path}")
    print(f"[train_chord_model] To use in TABsucks, pass --checkpoint {best_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
