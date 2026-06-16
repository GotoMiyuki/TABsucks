# ChordMini 全量集成开发日志

> 本文档记录 P0 阶段 ChordMini 和弦识别方案的完整集成：BTC-SL 插件升级、ChordNet (2E1D) 新插件、高级推理流水线接入、以及 AnalysisEngine 对齐。

---

## 一、模块概述

本次开发将 ChordMini 项目的**全部推理能力**整合进 TABsucks 插件系统，涉及四个层面：

### 1.1 btc_sl.py — BTC-SL 插件升级

位于 `src/plugins/chord/btc_sl.py`。从 v1.0.0 升级到 v2.0.0，核心变化：

- **Checkpoint 切换**：默认加载 ChordMini CL 训练的 `btc_model_best.pth`（替代原始 Teacher `btc_model_large_voca.pt`）
- **推理流水线升级**：从简单的 chunk-forward + argmax 升级为 ChordMini 的滑动窗口推理（overlap + 时序平滑 + 投票聚合）
- **Checkpoint 加载重构**：用 `extract_state_dict_and_stats()` 替代手动 dict 探测，兼容所有 checkpoint 格式

### 1.2 chordnet_2e1d.py — ChordNet (2E1D) 新插件

位于 `src/plugins/chord/chordnet_2e1d.py`。全新插件，封装 ChordMini 的 ChordNet 模型：

- **架构**：频率轴编码器 (EncoderF) + 时间轴编码器 (EncoderT) + 交叉注意力解码器 (Decoder)
- **Checkpoint**：使用 `2e1d_model_best.pth`（ChordMini CL 训练产出）
- **推理**：与 BTC-SL 共享同一套高级推理流水线

### 1.3 命名空间冲突解决

TABsucks 和 ChordMini 都使用 `src` 作为 Python 顶层包名，导致 `from src.models import ...` 无法在 TABsucks 进程中正确解析。通过 `src.__path__` 注入解决（详见第五节）。

### 1.4 AnalysisEngine 插件优先级更新

`_run_chord()` 的候选插件列表新增 `chord_chordnet_2e1d`，优先级最高。

### 核心依赖汇总

| 依赖 | 用途 |
|------|------|
| `torch` | BTC / ChordNet 模型推理 |
| `librosa` | CQT 特征提取 |
| `numpy` | 数值计算、游程编码 |
| `src.plugins.Plugin` | 插件基类 |
| `src.kernel.core.ResourceController` | 音频 buffer、模型缓存、元数据回写 |
| ChordMini `predict_sliding_windows` | 高级推理流水线 |
| ChordMini `extract_state_dict_and_stats` | Checkpoint 格式兼容 |

---

## 二、架构设计

### 2.1 和弦识别数据流（更新后）

```
┌──────────────────┐  {start, end, chord}  ┌─────────────────┐  list[ChordEvent]  ┌──────────────┐
│ chord_chordnet   │ ────────────────────▶ │                 │ ────────────────▶ │ Visualizer   │
│ _2e1d (优先)     │                       │  ChordAnalyzer  │                   │              │
└──────────────────┘                       │  (归一化层)      │                   └──────────────┘
┌──────────────────┐  {start, end, chord}  │                 │
│ chord_btc_sl     │ ────────────────────▶ │  normalize      │
│ (v2.0)           │                       │  _chord_label() │
└──────────────────┘                       │  build_chord    │
┌──────────────────┐  {start, end, chord}  │  _events()      │
│ chord_ismir2019  │ ────────────────────▶ │                 │
└──────────────────┘                       └─────────────────┘
```

### 2.2 高级推理流水线

```
音频 → CQT (144 bins, hop=2048)
     → Sliding Window (seq_len=108, overlap=50%)
     → 每个窗口: model.forward() → logits
     → Temporal Smoothing (Gaussian, kernel_size=9)
     → Logit Vote Aggregation (重叠区域累加)
     → argmax → 帧级预测
     → Majority Filter (可选)
     → 游程编码 → [{start, end, chord}, ...]
```

### 2.3 插件输出格式

三个插件统一输出 `{"start", "end", "chord"}` 格式，兼容 `build_chord_events()`。

---

## 三、核心改动详解

### 3.1 btc_sl.py 关键变更

#### `_setup_chordmini_imports()` — 命名空间注入

```python
def _setup_chordmini_imports():
    import src as _tabsucks_src
    if _CHORDMINI_SRC not in _tabsucks_src.__path__:
        _tabsucks_src.__path__.insert(0, _CHORDMINI_SRC)
```

将 ChordMini 的 `src/` 目录注入 TABsucks 的 `src.__path__`，使 `src.models` 等子模块能从 ChordMini 目录中被找到。

#### `_init_model()` — 多 Checkpoint 支持

- `_DEFAULT_CHECKPOINT_CANDIDATES`：按优先级列出候选 checkpoint，自动选择第一个存在的
- 缓存 key 改为 `f"btc_sl_model:{checkpoint_path}"`，支持不同 checkpoint 共存
- `execute()` 通过 `kwargs.get("checkpoint")` 支持显式指定 checkpoint

#### `_run_inference()` — 高级推理

```python
predictions = _predict_sliding_windows(
    model=model,
    feature_matrix=features,  # 未归一化，由流水线内部处理
    mean=mean, std=std,
    model_type="BTC",
    n_classes=170,
    vote_aggregation="logit",
    use_overlap=True, overlap_ratio=0.5,
    smooth_logits=True, smooth_predictions=True,
    kernel_size=9, use_gaussian=True,
)
```

#### `execute()` 新增 kwargs

| kwargs | 默认值 | 说明 |
|--------|--------|------|
| `checkpoint` | None (自动选择) | 显式 checkpoint 路径 |
| `seq_len` | 108 | 滑动窗口长度 |
| `batch_size` | 32 | 推理批大小 |
| `use_overlap` | True | 启用重叠窗口 |
| `overlap_ratio` | 0.5 | 重叠比例 |
| `smooth_kernel` | 9 | 时序平滑核大小 |
| `use_gaussian` | True | 使用高斯平滑 |

### 3.2 chordnet_2e1d.py 结构

与 btc_sl.py 完全对齐：

```
_EXTERNAL_DIR / _CHORDMINI_SRC / _CHECKPOINTS_DIR
_setup_chordmini_imports()
_ensure_imports()  → ChordNet, get_chordnet_config, predict_sliding_windows, extract_state_dict_and_stats
_extract_cqt_features()  → 从 btc_sl 导入（避免重复代码）

class ChordNet2E1DPlugin(BasePlugin):
    name = "chord_chordnet_2e1d"
    version = "1.0.0"
    _DEFAULT_CHECKPOINT = .../checkpoints/2e1d_model_best.pth

    execute(rc, **kwargs)  → 与 btc_sl 相同的推理流水线
    _init_model(rc, checkpoint_path)  → get_chordnet_config() + load_state_dict(strict=False)
    _run_inference(...)  → model_type="ChordNet", use_chordnet_defaults=True
```

### 3.3 `predict_sliding_windows` 的导入策略

ChordMini 的 `evaluation/utils/__init__.py` 做了 `from .common import *`，而 `common.py` 导入了 `librosa`。为避免在不需要 librosa 的场景（如测试环境）触发依赖，使用 `importlib.util.spec_from_file_location` 直接从文件加载 `inference.py`，绕过 `__init__.py` 的级联导入。

---

## 四、AnalysisEngine 集成

`src/kernel/core/analysis_engine.py` 的 `_run_chord()` 插件优先级更新：

```python
chord_plugin_names = [
    "chord_chordnet_2e1d",      # ChordNet 2E1D (CL 训练, 轻量)
    "chord_btc_sl",             # BTC-SL (CL 训练, 高精度)
    "chord_ismir2019",          # ISMIR2019 (子进程, 基线)
    "chord_analyzer_stem_aware", # StemAware (桩代码)
]
```

引擎依次尝试，使用第一个已注册的插件。

---

## 五、设计决策记录

### Q1: 为什么用 `src.__path__` 注入而不是修改子模块？

ChordMini 是 git submodule，修改其内部文件会导致子模块状态不干净，且上游更新时会被覆盖。`src.__path__` 注入是纯运行时操作，不修改任何文件。

### Q2: 为什么用 `importlib` 直接加载 `inference.py`？

ChordMini 的 `evaluation/utils/__init__.py` 做了 `from .common import *`，级联导入了 `librosa`、`mir_eval` 等重型依赖。`inference.py` 本身只需要 `torch`、`numpy` 和 `common.majority_filter_indices`。通过 `importlib` 直接加载文件，避免在测试环境等不需要完整依赖的场景中触发导入失败。

### Q3: 为什么 ChordNet 用 `strict=False` 加载权重？

ChordNet 的架构参数（层数、头数等）在不同 checkpoint 中可能不同。`get_chordnet_config()` 返回默认配置，`strict=False` 允许 checkpoint 中的参数与默认配置不完全匹配（ChordNet 类本身也在 `load_state_dict` 中处理了输出层不匹配的情况）。

### Q4: 为什么 BTC 默认切换到 `btc_model_best.pth`？

`btc_model_large_voca.pt` 是原始 Teacher 权重（来自 jayg996/BTC-ISMIR19），未经 ChordMini 的持续学习微调。`btc_model_best.pth` 是 ChordMini 在有标注数据上 CL 训练后的版本，精度更高。保留 `btc_model_large_voca.pt` 作为 fallback。

### Q5: 推理流水线中特征为什么不提前归一化？

ChordMini 的 `predict_sliding_windows()` 在每个滑动窗口内部做归一化（`feature_tensor = (feature_tensor - mean) / std`）。这确保了不同窗口的归一化统计量一致，且与 ChordMini 官方推理行为完全对齐。

---

## 六、测试覆盖

### 6.1 test_btc_sl.py（14 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestIdx2VocaChord | 8 | 12 root major、各 quality、N/X、越界 |
| TestRunLengthEncode | 4 | 单段、多段、空输入、时间连续性 |
| TestBTCSLChordPluginExecute | 2 | execute 输出格式、版本号 v2.0.0 |

### 6.2 test_chordnet_2e1d.py（5 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestChordNet2E1DPluginProperties | 2 | name、version 属性 |
| TestChordNet2E1DExecute | 3 | execute 输出格式、metadata 写入、模型缓存 |

### 6.3 测试策略

由于 `librosa` 未安装在测试环境中，execute 测试使用 monkeypatch mock 了：
- `_init_model`：用随机初始化的模型替代真实 checkpoint 加载
- `_extract_cqt_features`：用随机矩阵替代真实 CQT 提取
- `_predict_sliding_windows`：用随机预测替代真实推理

### 6.4 全量回归

所有 77 个相关测试通过（含 analysis、bass_root、resource_controller 测试）。

---

## 七、文件清单

```text
src/plugins/chord/
├── btc_sl.py              # [升级] v2.0.0, 高级推理 + 多 checkpoint
├── chordnet_2e1d.py       # [新建] ChordNet (2E1D) 插件
├── ismir2019.py           # [不变] ISMIR2019 基线插件
├── bass_root.py           # [不变] Bass 根音检测
├── chord_foundation.py    # [不变] StemAware (桩代码)
├── manifest.json          # [更新] 新增 chord_btc_sl + chord_chordnet_2e1d
└── external/
    └── chordmini/         # [不变] Git 子模块
        └── checkpoints/
            ├── btc_model_large_voca.pt   # Teacher (12 MB)
            ├── btc_model_best.pth        # BTC CL (35 MB)
            └── 2e1d_model_best.pth       # ChordNet CL (27 MB)

src/kernel/core/
└── analysis_engine.py     # [更新] 插件优先级列表

tests/unit/
├── test_btc_sl.py         # [更新] +2 个 execute 测试
└── test_chordnet_2e1d.py  # [新建] 5 个测试
```

---

## 八、已知限制与后续计划

| 项目 | 当前状态 | 后续 |
|------|----------|------|
| 推理性能 | CPU 较慢，GPU 加速待验证 | 接入 RC 的 VRAM 调度后可 GPU 加速 |
| ChordNet 架构推断 | 使用默认 config + strict=False | 可引入 `_infer_chordnet()` 做精确推断 |
| 测试环境 librosa | 测试中 mock 了 CQT 和推理 | CI 安装 librosa 后可启用集成测试 |
| Stage 2 训练 | 未实现 | 需要带标注的和弦数据集 |
| Ensemble | 未实现 | BTC + ChordNet 多模型投票 |
