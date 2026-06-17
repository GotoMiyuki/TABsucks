# Refiner 精炼模块与调性分析开发日志

> 本文档记录 BassProgression 升级、KeyAnalyzer 调性分析、Refiner 和弦精炼、AnalysisEngine 流水线重构的设计决策与技术细节，供团队成员查阅。

---

## 一、模块概述

本次开发的核心目标是**补齐 AnalysisEngine 流水线的后处理三环节**：低音进行检测 → 调性推断 → 和弦精炼。此前流水线在 bass_root（全局单一根音）之后直接存储结果，缺少节拍对齐、多轨合并、转位标记和调性分析。

### 1.1 涉及的四个层面

| 层面 | 组件 | 文件 | 操作 |
|------|------|------|------|
| 插件层 | BassRootPlugin 升级 | `src/plugins/chord/bass_root.py` | 修改 |
| 分析层 | KeyAnalyzer 调性分析 | `src/analysis/key.py` | **新建** |
| 分析层 | Refiner 和弦精炼 | `src/analysis/refiner.py` | **新建** |
| 编排层 | AnalysisEngine 流水线重构 | `src/kernel/core/analysis_engine.py` | 修改 |

### 1.2 核心依赖汇总

| 依赖 | 用途 |
|------|------|
| `src.analysis.chord.ChordEvent` | Bass 事件复用和弦事件结构（quality=""） |
| `src.analysis.chord.ROOT_NOTES` | 音名↔半音索引映射 |
| `src.analysis.beat.BeatTracker` / `BeatInfo` | 从拍点时间戳构建 BeatInfo |
| `src.analysis.rhythm.RhythmInfo` | 提供 global_bpm 用于拍点推算 |
| `numpy` | chroma 向量、Pearson 相关、bincount 众数 |
| `librosa` | pyin 基频检测（bass_root 插件内部） |

---

## 二、流水线变更

### 2.1 新旧流水线对比

```
旧: rhythm → separation → deep_rhythm → chord_per_stem → bass_root(str) → store

新: rhythm → beat_grid → separation → deep_rhythm → bass_progression
    → chord_per_stem → key_analysis → refine → store
```

### 2.2 各阶段说明

| 步骤 | 方法 | 输出 | 说明 |
|------|------|------|------|
| 1 | `_run_rhythm()` | `RhythmInfo` | 不变 |
| 2 | `_generate_beat_grid()` | `BeatInfo` | **新增**：从 BPM 数学推算逐拍时间戳 |
| 3 | `_run_separation()` | `SeparationResult` | 不变 |
| 4 | `_try_deep_rhythm()` | — | 不变（stub） |
| 5 | `_run_bass_progression()` | `list[ChordEvent]` | **改名**：从单根音改为低音进行序列 |
| 6 | `_run_chord(stem)` | `list[ChordEvent]` | 不变 |
| 7 | `_run_key_analysis()` | `KeyAnalysis` | **新增**：三维调性推断 |
| 8 | `_run_refine()` | `list[ChordEvent]` | **新增**：节拍对齐 → 多轨合并 → 转位标记 |

### 2.3 AnalysisResult 数据结构变更

```python
@dataclass
class AnalysisResult:
    rhythm: RhythmInfo | None = None
    beat_info: BeatInfo | None = None                                # 新增
    chord_events: dict[str, list[ChordEvent]] = field(default_factory=dict)
    bass_progression: list[ChordEvent] = field(default_factory=list) # 替代 bass_root: str
    key_analysis: KeyAnalysis | None = None                          # 新增
    unified_chords: list[ChordEvent] = field(default_factory=list)   # 新增
    separation_result: SeparationResult | None = None

    @property
    def bass_root(self) -> str:
        """从 bass_progression 推导全局根音（向后兼容）。"""
        ...
```

`bass_root: str` 字段被 `bass_progression: list[ChordEvent]` 替代，通过 `@property` 保留向后兼容访问。

---

## 三、BeatGrid — 拍点网格生成

### 3.1 设计决策：数学推算 vs 音频检测

| 方案 | 优点 | 缺点 |
|------|------|------|
| `librosa.beat.beat_track()` | 精确反映实际鼓点 | 引入新依赖，与现有 rhythm 插件功能重叠 |
| BPM 数学推算 | 零新依赖，复用已有 RhythmInfo | 假设匀速，不处理 tempo rubato |

**选择数学推算**。理由：rhythm 插件（`FoundationRhythmPlugin`）已基于 madmom 做了精密的 BPM 检测和 tempo map，拍点推算只需 `60/bpm` 递增即可。`librosa.beat.beat_track()` 是做鼓点定位（onset detection），与"每拍在第几秒"的拍子网格是不同概念。

### 3.2 实现

```python
def _generate_beat_grid(self, rhythm_info: RhythmInfo | None) -> BeatInfo:
    beat_duration = rhythm_info.beat_duration  # 60.0 / global_bpm
    audio_duration = len(raw_audio) / sr

    beat_timestamps = []
    t = 0.0
    while t < audio_duration:
        beat_timestamps.append(round(t, 6))
        t += beat_duration

    self._rc.set_metadata("beat_timestamps", beat_timestamps)
    tracker = BeatTracker(bpm=global_bpm, time_signature=time_sig)
    return tracker.track(beat_timestamps)
```

当 `global_bpm` 为 None（节奏分析失败）时跳过，`beat_timestamps` 设为空列表。

---

## 四、BassProgression — 低音进行检测

### 4.1 从单根音到时间序列

旧版 `BassRootPlugin` 用 `librosa.pyin()` 全曲取众数，输出单个音名 `"A"`。信息量极低，无法支撑调性推断。

新版按 beat 分段检测，输出 `list[ChordEvent]`：

```
旧: bass_root = "A"
新: bass_progression = [
      ChordEvent(root="E", quality="", start=0.0, end=0.5),
      ChordEvent(root="A", quality="", start=0.5, end=2.0),
      ChordEvent(root="D", quality="", start=2.0, end=3.0),
      ChordEvent(root="E", quality="", start=3.0, end=4.0),
    ]
```

### 4.2 算法

```
librosa.pyin() → F0 序列
  → librosa.times_like() 获取每帧时间
  → librosa.hz_to_midi() 转 MIDI → 四舍五入 → 过滤 [24, 72]
  → np.searchsorted(beat_timestamps, frame_times) 分配到 beat 区间
  → 每个 beat 区间取众数（np.bincount）
  → 跳过无 voiced 帧的区间
  → 相邻同 root（MIDI % 12 相同）的区间合并
  → 输出 ChordEvent(root=note_name, quality="", start, end)
```

### 4.3 向后兼容

- 无 `beat_timestamps` 时 fallback 到原 `_detect_root()` 单根音逻辑
- RC metadata 同时写 `"bass_root"` 和 `"bass_progression"`
- 返回 dict 同时包含 `"root"` 和 `"bass_progression"` key
- 版本号 v1.0.0 → v2.0.0

---

## 五、KeyAnalyzer — 调性分析

### 5.1 三维证据融合架构

```
Bass Progression
  │
  ├── 维度 1: Krumhansl-Schmuckler (权重 0.5)
  │     12 维 chroma 向量 × 24 个 key profile → Pearson 相关
  │
  ├── 维度 2: 终止式模式匹配 (权重 0.3)
  │     末尾 2 音 × 12 候选调性 × 4 种终止式
  │
  └── 维度 3: 功能和声分析 (权重 0.2)
        T/S/D 标记 → 转换合法性评分
  │
  ▼
KeyAnalysis(key, mode, confidence, ks_correlation, cadence_type, functional_coherence)
```

### 5.2 维度 1 — Krumhansl-Schmuckler

经典调性检测算法。从 bass progression 构建 12 维 chroma 向量（按 `ChordEvent.duration` 加权），与 24 个 key profile 做 Pearson 相关，取最高分。

```python
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
```

通过 `np.roll(chroma, -shift)` 将 chroma 向量移位，等价于"假设调性是 ROOT_NOTES[shift]"。

### 5.3 维度 2 — 终止式模式匹配

| 终止式 | 模式（半音偏移） | 置信度 |
|--------|------------------|--------|
| 正格终止 (Authentic) | V→I (7→0) | 0.9 |
| 变格终止 (Plagal) | IV→I (5→0) | 0.7 |
| 半终止 (Half) | I→V (0→7) | 0.6 |
| 阻碍终止 (Deceptive) | V→vi (7→9) | 0.5 |

取 bass progression 末尾 2 个事件，遍历 12 个候选调性匹配。

**注意**：某些音程组合存在固有歧义。例如 C→G 同时匹配 C 大调的半终止 (I→V, conf=0.6) 和 G 大调的变格终止 (IV→I, conf=0.7)。算法选择置信度更高的匹配，这是合理的音乐学行为。

### 5.4 维度 3 — 功能和声分析

将每个 bass 音映射到候选调性的功能标签（T=主/S=下属/D=属），统计相邻功能转换的合法性：

```python
_MAJOR_FUNCTION = {0:"T", 2:"S", 4:"T", 5:"S", 7:"D", 9:"T", 11:"D"}
_MINOR_FUNCTION = {0:"T", 2:"D", 3:"S", 5:"S", 7:"D", 8:"T", 10:"D"}

_GOOD_TRANSITIONS = {("T","S"), ("S","D"), ("D","T"), ("T","T"), ("S","S"), ("T","D")}
```

Coherence = 合法转换数 / 总转换数。

### 5.5 加权融合

```python
if cad_key == ks_key:
    confidence = 0.5 * max(ks_corr, 0) + 0.3 * cad_conf + 0.2 * func_score
else:
    confidence = 0.5 * max(ks_corr, 0) + 0.2 * func_score
```

终止式与 K-S 一致时叠加权重，否则忽略终止式维度（避免错误的终止式匹配干扰结果）。

---

## 六、Refiner — 和弦精炼

### 6.1 整体流程

```
chord_events: dict[str, list[ChordEvent]]   (per-stem 原始和弦)
  │
  ├── Step 1: snap_to_beats()               逐 stem 节拍对齐 + 去抖动
  │
  ├── Step 3: merge_stem_chords()           piano + guitar → 统一视图
  │
  └── Step 4: mark_inversions()             bass root ≠ chord root → slash chord
  │
  ▼
unified_chords: list[ChordEvent]            精炼后的统一和弦序列
```

### 6.2 Step 1 — BeatSync 节拍对齐

```python
def snap_to_beats(events, beat_timestamps) -> list[ChordEvent]:
```

对每个 `ChordEvent` 的 `start`/`end` 找最近的 beat 边界，然后合并相邻且 `root+quality` 相同的事件。

目的：消除模型输出的帧级抖动（同一和弦在相邻帧间闪烁），使和弦变化点落在节拍边界上。

### 6.3 Step 3 — 多轨合并

```python
def merge_stem_chords(piano, guitar) -> list[ChordEvent]:
```

从两条轨的所有 `start`/`end` 构建统一时间轴，每个时间段选取最佳和弦：

| 情况 | 策略 |
|------|------|
| root 一致 | 取 quality 字符串更长的（信息量更丰富） |
| root 不一致 | 取 duration 更长的（更"稳"的那个） |

### 6.4 Step 4 — 转位标记

```python
def mark_inversions(unified, bass_progression) -> list[ChordEvent]:
```

对每个 unified chord，找时间重叠的 bass 事件。当 bass root ≠ chord root 时标记为 slash chord：

```python
# C 和弦 + bass E → root="C", quality="/E" → name="C/E"
# Cm7 和弦 + bass E → root="C", quality="m7/E" → name="Cm7/E"
```

---

## 七、测试覆盖

### 7.1 test_key.py（19 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestKeyAnalysisDataclass | 2 | frozen 不可变、字段完整性 |
| TestKrumhanslSchmuckler | 3 | C 大调音阶、A 小调（含小三度 C）、G 大调 |
| TestCadenceAnalysis | 5 | 正格(E→A)、变格(F→C)、阻碍(G→A)、半终止(E→A)、无终止(C→F#)、空/单音 |
| TestFunctionalAnalysis | 3 | T→S→D→T 高一致性、杂乱低一致性、单音无转换 |
| TestAnalyzeKey | 4 | 明确 C 大调、含终止式、空进行、N/X 过滤 |

### 7.2 test_refiner.py（14 个测试）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestNearestBeat | 3 | 精确命中、偏前、偏后 |
| TestMergeAdjacent | 4 | 合并同 root、不同 root 不合并、不同 quality 不合并、空列表 |
| TestSnapToBeats | 4 | 基本 snap、snap 后合并、空事件、空 beat |
| TestMergeStemChords | 4 | 同 root 取丰富 quality、不同 root 取长 duration、单空、双空 |
| TestMarkInversions | 6 | C/E 转位、同 root 不变、Cm7/E、无 bass 重叠、空 chords、空 bass |
| TestRefinePipeline | 2 | 完整流程、无数据 |

### 7.3 test_bass_root.py（更新，11 个测试）

| 测试类 | 数量 | 变更 |
|--------|------|------|
| TestBassRootPlugin | 2 | version 更新为 2.0.0 |
| TestDetectRoot | 3 | 不变（向后兼容） |
| TestDetectProgression | 3 | **新增**：基本 progression、相邻合并、静音空列表 |
| TestExecute | 3 | **新增**：返回 root+progression、无 beat fallback、RC 向后兼容 |

### 7.4 test_analysis_engine.py（更新，13 个测试）

| 测试 | 变更 |
|------|------|
| test_full_pipeline | 适配 bass_progression + beat_info + key_analysis |
| test_progress_callback | 新增 beat_grid / key_analysis / refine 步骤检查 |
| test_no_plugins_still_works | 新增 bass_progression / key_analysis / unified_chords 默认值 |
| test_beat_grid_from_bpm | **新增**：BPM=120, 2s → 4 个拍点 |
| test_refine_runs_with_data | **新增**：refine 不报错 |
| test_default_values | 新增 beat_info / key_analysis / unified_chords 默认值 |
| test_bass_root_backward_compat | **新增**：bass_root property 从 progression 推导 |
| test_bass_root_empty_progression | **新增**：空 progression → "N" |

### 7.5 汇总

本次新增/修改的 57 个测试全部通过。完整测试套件 216 个测试通过，零回归。

---

## 八、设计决策记录

### Q1: BassEvent 为什么复用 ChordEvent 而不是新建独立类型？

低音进行只有 root（音高）和时间区间，ChordEvent 的 `quality=""` 可以精确表达这一点。复用的好处是下游模块（snap_to_beats、mark_inversions 等）可以直接处理 ChordEvent 列表，无需为 BassEvent 单独写一套对齐/合并逻辑。

### Q2: 拍点网格为什么用 BPM 数学推算而不是 librosa.beat.beat_track()？

两个原因：

1. **概念不同**：`librosa.beat.beat_track()` 做的是鼓点定位（onset detection），输出的是"鼓在第几秒响"。拍点网格是"每拍在第几秒"，是一个均匀的时间网格，与鼓的实际击打位置无关。
2. **不重复造轮子**：rhythm 插件（`FoundationRhythmPlugin`）已用 madmom 做了精密的 BPM 检测。拍点推算只需 `60/bpm` 递增，是纯数学操作。

### Q3: Refiner 为什么是独立模块而不是插件？

Refiner 不处理音频，不需要 RC 的 buffer 访问或 GPU 模型管理。它的输入是结构化的 `ChordEvent` 列表和拍点时间戳，输出也是 `ChordEvent` 列表。作为 `src/analysis/` 下的纯函数模块，与 `chord.py`、`key.py` 并列，比注册为插件更简洁。

### Q4: 转位标记为什么编码在 quality 字段里？

`ChordEvent` 是 frozen dataclass，不能事后修改字段。slash chord 信息（如 C/E）必须编码在现有字段中。`quality="/E"` 使得 `ChordEvent.name` 返回 `"C/E"`，`quality="m7/E"` 返回 `"Cm7/E"`，与标准乐谱记法一致。前端展示层可直接使用 `name` 属性。

### Q5: 终止式匹配的歧义问题如何处理？

某些音程组合存在固有歧义（如 C→G 同时匹配 C 大调半终止和 G 大调变格终止）。算法选择置信度更高的匹配（变格 0.7 > 半终止 0.6），这是合理的——变格终止在实际音乐中比半终止更能确认调性。在三维融合中，终止式维度只占 0.3 权重，即使单维度有歧义也不会主导最终结果。

### Q6: AnalysisResult.bass_root 为什么用 @property 而不是直接保留字段？

`bass_progression: list[ChordEvent]` 包含了 `bass_root: str` 的全部信息（取众数即可推导）。保留两个字段会造成数据冗余和不一致风险。`@property` 方式既避免了冗余，又让已有的 `result.bass_root` 访问代码无需修改。

---

## 九、已知限制与后续计划

| 项目 | 当前状态 | 后续 |
|------|----------|------|
| 拍点网格 | BPM 匀速推算 | 可用 `bpm_map`（tempo map）做变速拍点推算 |
| 调性推断 | 仅基于 bass progression | 可融合 chord_events 的功能分析提升精度 |
| roman_numeral | 仍为 TODO stub | 接入 `key_analysis.key` 后可实现：用半音偏移查表 |
| 多轨合并 | 仅支持 piano + guitar | 可扩展为任意 stem 数量的合并 |
| 转位标记 | 取众数 bass root，粒度较粗 | 可用逐 beat 的 bass_progression 做精细化转位检测 |
| deep_rhythm | 仍为 stub | Phase C 完成后注册 `rhythm_deep` 插件 |
| 集成测试 | 仅 mock 测试 | 需真实音频端到端验证 |
