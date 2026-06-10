# 和弦识别层重构与插件补全开发日志

> 本文档记录 `analysis/chord.py` 归一化层重构、`bass_root.py` 根音检测实现、`btc_sl.py` BTC-SL 和弦识别插件实现的设计决策与技术细节，供团队成员查阅。

---

## 一、模块概述

本次开发聚焦于**和弦识别子系统**的架构对齐与功能补全，涉及三个层面：

### 1.1 analysis/chord.py — 归一化层重构

位于 `src/analysis/chord.py`。参照 `beat.py` 的"承载+归一化"模式，将 `ChordAnalyzer` 从自生成占位数据的桩代码重构为**轻量数据归一化器**。

**设计原则**（与 beat.py 对齐）：
- 实际和弦识别由 `plugins/chord/` 下的各插件负责
- 本模块只负责承载数据结构（`ChordEvent`）、归一化（`normalize_chord_label`）、转换（`build_chord_events`）
- 对接 `AnalysisEngine` 和 `ResourceController`

### 1.2 bass_root.py — Bass 根音检测插件

位于 `src/plugins/chord/bass_root.py`。从 bass 轨检测根音的轻量级辅助插件，使用 `librosa.pyin` 基频检测。

### 1.3 btc_sl.py — BTC-SL 和弦识别插件

位于 `src/plugins/chord/btc_sl.py`。基于 [ChordMini](https://github.com/ptnghia-j/ChordMini) 项目的 BTC（Bi-directional Transformer for Chords）Self-Label 模型，使用 170 类大词汇表进行和弦识别。

### 核心依赖汇总

| 依赖 | 用途 |
|------|------|
| `torch` | BTC-SL 模型推理 |
| `librosa` | CQT 特征提取（btc_sl）、pyin 基频检测（bass_root） |
| `numpy` | 数值计算、游程编码 |
| `src.plugins.Plugin` | 插件基类 |
| `src.kernel.core.ResourceController` | 音频 buffer 读取、模型缓存、元数据回写 |

---

## 二、架构设计

### 2.1 和弦识别数据流

```
┌─────────────┐  {start, end, chord}  ┌─────────────────┐  list[ChordEvent]  ┌──────────────┐
│ ISMIR2019   │ ────────────────────▶ │                 │ ────────────────▶ │ Visualizer   │
│ Plugin      │                       │  ChordAnalyzer  │                   │ (build_chord │
└─────────────┘                       │  (归一化层)      │                   │  _labels)    │
┌─────────────┐  {start, end, chord}  │                 │                   └──────────────┘
│ BTC-SL      │ ────────────────────▶ │  normalize      │
│ Plugin      │                       │  _chord_label() │
└─────────────┘                       │  build_chord    │
┌─────────────┐  {time, chord}        │  _events()      │
│ chord_      │ ────────────────────▶ │                 │
│ foundation  │                       │                 │
└─────────────┘                       └─────────────────┘
```

### 2.2 插件输出格式兼容

三个和弦插件输出两种 dict 格式，`build_chord_events()` 同时兼容：

| 插件 | 格式 | 示例 |
|------|------|------|
| ISMIR2019 / BTC-SL | `start` + `end` + `chord` | `{"start": 0.0, "end": 2.0, "chord": "Am7"}` |
| chord_foundation | `time` + `chord` | `{"time": 0.0, "chord": "C:maj7"}` |

检测逻辑：检查第一个元素是否有 `start` 键。对于只有 `time` 的格式，通过相邻事件自动推算 `end`。

### 2.3 和弦标签归一化

`normalize_chord_label()` 处理多种标注风格：

```
"Am7"     → ("A", "m7")       # 无分隔符
"C#dim"   → ("C#", "dim")     # 升号根音
"C:maj7"  → ("C", "maj7")     # 冒号分隔（ISMIR2019 标准格式）
"N"       → ("N", "")         # 无和弦
""        → ("N", "")         # 空字符串
```

实现方式：预排序的根音前缀集合（先匹配两字符 `C#`，再匹配单字符 `C`），余下部分作为 quality。

---

## 三、BTC-SL 插件实现细节

### 3.1 模型架构

BTC-SL 使用 **Bi-directional Self-Attention** 架构（非标准 Transformer Encoder）：

- 每层同时运行**正向因果注意力**和**反向因果注意力**，拼接后线性投影
- 8 层堆叠 + SoftmaxOutputLayer
- 输入：CQT 144 bins（6 octave × 24 bins/octave，从 C1 起），log-magnitude
- 输出：170 类（12 root × 14 quality + N + X）帧级预测

### 3.2 170 类词汇表映射

```python
# 索引 = root_idx * 14 + quality_idx
# quality_list: min, maj, dim, aug, min6, maj6, min7, minmaj7, maj7, 7, dim7, hdim7, sus2, sus4
# 特殊: 168→X, 169→N
```

与 ISMIR2019 的 301 类词汇表不同，BTC-SL 使用更紧凑的 170 类。quality 中 `maj` 类输出时省略后缀（如 `"C"` 而非 `"C:maj"`）。

### 3.3 推理流程

```
音频 → librosa CQT (sr=22050, 144 bins, hop=2048)
     → log(|cqt| + 1e-6).T → (frames, 144)
     → checkpoint mean/std 归一化
     → 切 108 帧 chunk → BTC_model.forward() → argmax
     → 游程编码 → [{start, end, chord}, ...]
```

### 3.4 ChordMini 子模块集成

参照 ismir2019 的子模块模式，将 ChordMini 仓库添加为 git submodule：

```
src/plugins/chord/external/chordmini/   ← git submodule
pretrained/btc_model_large_voca.pt      ← ~12MB 权重文件
```

通过 `sys.path.insert` 将子模块根目录加入 Python 路径，延迟导入避免启动时加载 torch 等重型依赖。模型实例通过 `ResourceController.set_metadata` 缓存复用。

### 3.5 Checkpoint 格式兼容

原版 BTC checkpoint 格式不统一，插件兼容三种情况：

```python
state_dict = checkpoint.get("model_state_dict", checkpoint.get("model", checkpoint))
```

同时处理 `module.` 前缀（DDP 训练产出的权重）。

---

## 四、Bass Root 插件实现细节

### 4.1 算法

使用 `librosa.pyin`（Probabilistic YIN）进行基频检测：

1. 对 bass 轨音频运行 pyin（fmin=E1, fmax=E4）
2. 过滤未发声帧（voiced_flag=False）
3. Hz → MIDI 编号 → 四舍五入取最近半音
4. 过滤超出 bass 吉他范围的值（MIDI 24-72，C1-C5）
5. 取众数（`np.bincount().argmax()`）作为根音

### 4.2 输出格式

```python
{"status": "success", "root": "A"}   # 正常
{"status": "success", "root": "N"}   # 无声
```

作为辅助插件，结果通过 `rc.set_metadata("bass_root", root)` 回写，供其他分析模块使用。

---

## 五、测试覆盖

### 5.1 test_analysis.py（31 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestChordEvent | 3 | name、duration 属性 |
| TestNormalizeChordLabel | 11 | 三和弦、七和弦、升降号、冒号分隔、N/X、空字符串 |
| TestBuildChordEvents | 4 | ISMIR 格式、foundation 格式、空列表、未知格式异常 |
| TestChordAnalyzer | 5 | dict 列表、chords/data 属性对象、音频对象拒绝、根音合法性 |
| TestBeatEvent | 2 | 4/4 拍、3/4 拍小节计算 |
| TestBeatTracker | 4 | 初始化、拍号归一化、track 构建、音频检测拒绝 |
| TestRhythmAnalyzer | 2 | 分析返回、dominant pattern |

### 5.2 test_btc_sl.py（12 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestIdx2VocaChord | 8 | 12 root major、各 quality、N/X、越界 |
| TestRunLengthEncode | 4 | 单段、多段、空输入、时间连续性 |

### 5.3 test_bass_root.py（5 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestBassRootPlugin | 2 | name、version 属性 |
| TestDetectRoot | 3 | 静音→N、440Hz→A、Plugin 接口合规 |

### 5.4 下游兼容性验证

visualizer 测试（34 个）全部通过，确认 `ChordEvent` 重构未破坏 `build_chord_labels()` 和 `export_visualization_json()` 的 duck-typed 接口。

---

## 六、设计决策记录

### Q1: chord.py 为什么重构？

beat.py 已完成从"自己做检测"到"接收插件结果 + 归一化"的转型。chord.py 的 `ChordAnalyzer.analyze()` 仍在内部生成假数据，与架构分层不一致。重构后三个 analysis 模块（beat、chord、rhythm）遵循同一模式。

### Q2: BTC-SL 模型代码为什么用 git submodule 而不是直接嵌入？

ChordMini 仓库包含模型定义、训练脚本、评估工具等大量代码。直接嵌入会使 btc_sl.py 膨胀且难以追踪上游更新。submodule 方式保持了代码来源的可追溯性，与 ismir2019 子模块的模式一致。

### Q3: build_chord_events 为什么兼容两种格式？

chord_foundation 输出 `{time, chord}`（单时间点），而 ismir2019/btc_sl 输出 `{start, end, chord}`（时间区间）。在 chord_foundation 暂时搁置的现状下，兼容两种格式避免了后续接入时的格式转换成本。

### Q4: bass_root 为什么用 librosa.pyin 而不是更复杂的模型？

bass_root 定位为轻量辅助插件——只检测根音，不识别和弦性质。pyin 是成熟的基频检测算法，对单音 bass 轨已经足够，无需引入重型模型。

---

## 七、已知限制与后续计划

| 项目 | 当前状态 | 后续 |
|------|----------|------|
| chord_foundation.py | 暂时搁置 | 等有训练好的 StemChordFormer 权重后接入 |
| BTC-SL 推理性能 | CPU 推理较慢 | 接入 ResourceController 的 VRAM 调度后可 GPU 加速 |
| roman_numeral 属性 | 仍为 TODO | 需接入调性分析模块 |
| rhythm.py 归一化 | 未改动 | 等 Phase C 节奏插件完成后对齐 beat.py/chord.py 模式 |
| bass_root 精度 | 众数法，对变化频繁的 bass line 可能不够精确 | 可考虑加入时间窗口分段检测 |
