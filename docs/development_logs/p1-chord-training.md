# 和弦识别训练流水线开发日志

> 本文档记录 P1 阶段和弦识别模型 Stage 1 伪标签训练流水线的集成方案、使用方法与设计决策。

---

## 一、概述

基于 ChordMini 的两阶段知识蒸馏训练流水线，实现自定义和弦识别模型的训练能力。

### 1.1 训练流程

```
Stage 1: 伪标签训练 (Pseudo-Label Training)
  Teacher: btc_model_large_voca.pt (原始 BTC 预训练权重)
    ↓ 在线生成伪标签
  Student: ChordNet (2E1D) 或 BTC
    ↓ 在无标注数据上用伪标签训练

Stage 2: 持续学习 (Continual Learning) — 未来实现
  在有标注数据上微调 + Selective KD
```

### 1.2 文件清单

```
scripts/
├── train_chord_model.py        # Stage 1 训练包装脚本
└── download_training_data.py   # FMA 数据下载脚本

pretrained/
└── chordmini_trained/          # 训练输出目录（自动创建）
    └── chordnet/ 或 btc/
        └── best_model.pth      # 最佳 checkpoint
```

---

## 二、快速开始

### 2.1 下载训练数据

```bash
# 下载 FMA small 子集 (~7.2 GB, 8000 tracks)
py scripts/download_training_data.py --subset small

# 小规模测试 (500 tracks)
py scripts/download_training_data.py --subset small --max_tracks 500
```

数据将下载到 `data/fma_audio/`，UnlabeledAudioDataset 会递归扫描其中的 `.mp3/.wav/.flac` 文件。

### 2.2 训练 ChordNet (推荐)

```bash
py scripts/train_chord_model.py \
    --audio_dir data/fma_audio \
    --model_type ChordNet \
    --use_kd \
    --max_files 500 \
    --batch_size 64 \
    --num_epochs 50
```

### 2.3 训练 BTC

```bash
py scripts/train_chord_model.py \
    --audio_dir data/fma_audio \
    --model_type BTC \
    --max_files 500 \
    --batch_size 64 \
    --num_epochs 50
```

### 2.4 使用训练好的模型

训练完成后，checkpoint 保存在 `pretrained/chordmini_trained/<model_type>/best_model.pth`。

在插件中使用：
```python
plugin = ChordNet2E1DPlugin()  # 或 BTCSLChordPlugin()
result = plugin.execute(rc, stem_name="piano",
    checkpoint="pretrained/chordmini_trained/chordnet/best_model.pth")
```

---

## 三、关键参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--audio_dir` | (必填) | 无标注音频目录 |
| `--model_type` | ChordNet | Student 模型类型 |
| `--max_files` | None | 限制音频文件数（调试用） |
| `--use_kd` | False | 启用知识蒸馏 loss |
| `--batch_size` | 256 | 批大小 |
| `--num_epochs` | 100 | 最大训练轮数 |
| `--learning_rate` | 1e-4 | 学习率 |
| `--use_focal_loss` | True | 使用 Focal Loss |
| `--seq_len` | 108 | 序列长度（帧数） |

### ChordNet 架构覆盖参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--n_group` | 12 | 频率轴分组数 |
| `--f_layer` | 3 | 频率编码器层数 |
| `--t_layer` | 4 | 时间编码器层数 |
| `--d_layer` | 3 | 解码器层数 |
| `--dropout` | 0.3 | Dropout 率 |

---

## 四、数据源

### Stage 1 无标注数据（公开可下载）

| 数据集 | 规模 | 许可 |
|--------|------|------|
| FMA small | 8000 tracks, ~7.2 GB | CC-BY |
| FMA medium | 25000 tracks, ~22 GB | CC-BY |
| DALI | ~5000 tracks | 学术用途 |
| MAESTRO | ~1200 piano tracks | CC-BY-NC |

### Stage 2 有标注数据（需自行准备）

| 数据集 | 规模 | 格式 |
|--------|------|------|
| Isophonics | ~300 tracks | .lab |
| McGill Billboard | 890 tracks | .lab |
| MIREX | 基准测试集 | .lab |

标注格式：
```
0.000000 1.234567 C:maj
1.234567 2.345678 G:7
```

---

## 五、设计决策

### Q1: 为什么用包装脚本而不是直接调用 ChordMini 的训练脚本？

ChordMini 的 `train_pseudo_labeling.py` 使用 argparse，直接调用会与包装脚本冲突。通过导入 ChordMini 的组件并手动编排，我们可以：
- 控制输出路径（存到 `pretrained/` 而非子模块内）
- 提供简化的 CLI 参数
- 在训练后自动打印使用说明

### Q2: 为什么用 `src.__path__` 注入而不是 `sys.path.insert`？

TABsucks 和 ChordMini 都使用 `src` 作为顶层包名。TABsucks 的 `src` 有 `__init__.py`，会覆盖 ChordMini 的 `src`。通过将 ChordMini 的 `src` 目录注入到 `src.__path__`，Python 的导入系统会在两个目录中搜索子模块，解决命名空间冲突。

### Q3: checkpoint 存储位置为什么在子模块外？

子模块 `external/chordmini/` 是 git submodule，其内容可能在 `git submodule update` 时被覆盖。将训练产出存到 `pretrained/chordmini_trained/` 确保 checkpoint 不会丢失。

---

## 六、资源需求

| 项目 | 最低要求 | 推荐 |
|------|----------|------|
| GPU VRAM | 4 GB | 8 GB+ |
| RAM | 8 GB | 16 GB+ |
| 磁盘 | 10 GB | 50 GB+ |
| 训练时间 (500 tracks, 50 epochs) | ~30 min (GPU) | - |
| 训练时间 (8000 tracks, 100 epochs) | ~8 hr (GPU) | - |
