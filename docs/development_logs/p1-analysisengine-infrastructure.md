# AnalysisEngine 基础设施搭建开发日志

> 本文档记录插件层统一、RhythmAnalyzer 重构、AnalysisEngine 编排引擎实现的设计决策与技术细节，供团队成员查阅。

---

## 一、模块概述

本次开发的核心目标是**让 AnalysisEngine 从伪代码变成可运行的真实类**，补齐"节奏分析→音轨分离→和弦识别→bass 根音检测"主流程所需的全部基础设施。

### 1.1 涉及的三个层面

| 层面 | 组件 | 文件 |
|------|------|------|
| 插件层 | Plugin ABC 签名统一、PluginManager 迁移 | `src/plugins/__init__.py`、`src/kernel/core/plugin_manager.py` |
| 分析层 | RhythmAnalyzer 重构为归一化模式 | `src/analysis/rhythm.py` |
| 编排层 | AnalysisEngine 类实现 | `src/kernel/core/analysis_engine.py` |

### 1.2 核心依赖汇总

| 依赖 | 用途 |
|------|------|
| `src.plugins.Plugin` | 统一后的插件基类 |
| `src.kernel.core.ResourceController` | 插件间共享状态总线 |
| `src.analysis.rhythm.RhythmInfo` | 节奏分析结果容器 |
| `src.analysis.chord.ChordEvent` | 和弦事件数据结构 |
| `src.plugins.separation.separator` | BS-RoFormer 音轨分离（延迟导入） |

---

## 二、插件层统一

### 2.1 问题：Plugin ABC 签名不一致

统一前的现状：

```
Plugin ABC 定义：    execute(self, audio_data, **kwargs)
实际插件实现：       execute(self, rc: ResourceController, **kwargs)
```

所有插件（ISMIR2019、BTC-SL、bass_root、FoundationRhythm）都覆盖了签名，接收 `rc` 而非 `audio_data`。ABC 的定义与实际行为不一致。

**修复**：将 `Plugin.execute` 签名改为 `(self, rc: ResourceController, **kwargs)`，使用 `TYPE_CHECKING` 避免循环导入。

### 2.2 PluginManager 位置迁移

原来 `src/plugins/__init__.py` 中同时定义了 Plugin 基类和 PluginManager，但 PluginManager 是 AnalysisEngine 的依赖，属于 kernel 层。

**改动**：
- 删除 `src/plugins/__init__.py` 中的 PluginManager
- 在 `src/kernel/core/plugin_manager.py` 新实现，核心差异是 `execute()` 自动注入 RC：

```python
class PluginManager:
    def __init__(self, rc: ResourceController) -> None: ...

    def execute(self, name: str, **kwargs) -> dict:
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginManagerError(f"插件不存在: {name}")
        return plugin.execute(self._rc, **kwargs)  # 自动注入 rc
```

调用方不再需要手动传递 `rc`，AnalysisEngine 只需 `pm.execute("chord_ismir2019", stem_name="piano")`。

### 2.3 节奏插件清理

发现三个重复的 `FoundationRhythmPlugin` 定义：

| 文件 | 版本 | 基类 | 使用 RC |
|------|------|------|---------|
| `src/plugins/rhythm/foundation.py` | v1.2.0 | BasePlugin | 是 |
| `src/plugins/rhythm/rhythm_foundation.py` | v1.1.0 | Plugin | 否 |
| `src/plugins/foundation.py` | v1.1.0 | Plugin | 否 |

**保留** `src/plugins/rhythm/foundation.py`（v1.2.0），废弃后两份（重命名为 `.deprecated`）。

同时修复 `src/plugins/rhythm/_init_.py` → `__init__.py`（单下划线 → 双下划线），使其成为合法的 Python 包初始化文件。

---

## 三、RhythmAnalyzer 重构

### 3.1 设计模式对齐

`beat.py` 和 `chord.py` 已完成从"自己做检测"到"接收插件结果 + 归一化"的转型。`rhythm.py` 仍返回硬编码的 `RhythmType.PLAIN`，需要对齐。

### 3.2 新增数据结构

```python
@dataclass(frozen=True)
class RhythmInfo:
    """节奏分析结果容器（对标 BeatInfo / ChordEvent）。"""
    global_bpm: float | None = None
    time_signature: str = "4/4"
    complexity_score: float = 0.0
    needs_deep_analysis: bool = False
    bpm_map: list[tuple[float, float]] = field(default_factory=list)
```

与 `FoundationRhythmPlugin` 的输出字段一一对应。

### 3.3 归一化函数

`build_rhythm_info()` 兼容两种插件输出格式：

- 包装格式：`{"status": "success", "data": {"global_bpm": 120.0, ...}}`
- 直接格式：`{"global_bpm": 120.0, "time_signature_guess": "4/4", ...}`

通过 `_extract_data_dict()` 自动检测。

### 3.4 RhythmAnalyzer 重写

```python
class RhythmAnalyzer:
    def analyze(self, rhythm_source: dict | object) -> RhythmInfo:
        info = build_rhythm_info(rhythm_source)
        self._infer_patterns(info)  # 基于拍号推断节奏型
        return info
```

`RhythmPattern` 和 `get_dominant_pattern` 保留——目前基于拍号做简单映射（3 拍→WALTZ，4 拍→PLAIN），等 deep_rhythm 接入后会替换为真实的节奏型识别。

---

## 四、AnalysisEngine 实现

### 4.1 整体架构

```
AnalysisEngine
  ├── run()                          # 流水线入口
  │   ├── _run_rhythm()              # 1. 节奏分析（前置侦察兵，基于原始混音）
  │   ├── _run_separation()          # 2. 音轨分离 + RC 桥接
  │   ├── _try_deep_rhythm()         # 3. deep_rhythm 预留（分离后，可访问鼓声轨）
  │   ├── _run_chord(stem)           # 4. 和弦识别（piano/guitar）
  │   ├── _run_bass_root()           # 5. bass 根音检测
  │   └── 返回 AnalysisResult
  │
  └── 依赖
      ├── ResourceController         # 共享状态
      ├── PluginManager              # 插件调度
      ├── RhythmAnalyzer             # 节奏归一化
      └── ChordAnalyzer              # 和弦归一化
```

**执行顺序说明**：

1. **节奏分析**先于分离——基于原始混音做粗略 BPM/拍号检测（"前置侦察兵"）
2. **音轨分离**产出 6 个单独声轨
3. **deep_rhythm** 在分离之后执行——因为深度节奏分析需要访问鼓声轨等单独乐器数据，基于 complexity_score > 0.6 触发
4. **和弦识别**对 piano、guitar 分别执行
5. **bass 根音**检测

### 4.2 AnalysisResult 数据结构

```python
@dataclass
class AnalysisResult:
    rhythm: RhythmInfo | None = None
    chord_events: dict[str, list[ChordEvent]] = field(default_factory=dict)
    bass_root: str = "N"
    separation_result: SeparationResult | None = None
```

一次 `run()` 的全部结果汇总，存入 `rc.set_metadata("analysis_result", result)` 供下游使用。

### 4.3 分离步骤的 RC 桥接

`Separator` 不写 RC，AnalysisEngine 在 `_run_separation()` 中负责桥接：

```python
def _run_separation(self):
    separator = Separator()
    result = separator.separate(audio_data)
    for track_id in TrackId:
        self._rc.set_buffer(track_id.value, result.get_track(track_id))
    self._rc.set_metadata("separation_result", result)
```

### 4.4 和弦插件选择策略

`_run_chord()` 按优先级尝试已注册的和弦插件：

```python
chord_plugin_names = ["chord_btc_sl", "chord_ismir2019", "chord_analyzer_stem_aware"]
for name in chord_plugin_names:
    if self._pm.get(name) is not None:
        return self._pm.execute(name, stem_name=stem)
```

第一个可用的插件会被使用，未注册时返回空列表。

### 4.5 deep_rhythm 预留接口

```python
def _try_deep_rhythm(self) -> None:
    # TODO: 等 deep_rhythm 插件实现后取消注释
    # deep_plugin = self._pm.get("rhythm_deep")
    # if deep_plugin is not None:
    #     deep_plugin.execute(self._rc)
    pass
```

deep_rhythm 在**音轨分离之后**执行——它需要访问鼓声轨等单独乐器数据。当 `rhythm_info.needs_deep_analysis` 为 True 时，`run()` 会调用此方法。由于 PluginManager.get() 在插件不存在时返回 None，整个流程不会因缺少 deep_rhythm 插件而中断。

### 4.6 进度回调

```python
def run(self, progress_callback=None) -> AnalysisResult:
    self._report(progress_callback, "rhythm", 0.0)
    ...
    self._report(progress_callback, "rhythm", 1.0)
```

`progress_callback(step: str, progress: float)` 供 UI 层接入进度条。和弦识别阶段按 stem 数量细分进度。

### 4.7 延迟导入策略

`analysis_engine.py` 对重量级依赖使用延迟导入：

- `SeparationResult` / `TrackId` — 通过 `TYPE_CHECKING` + `_run_separation()` 内部 `from ... import`
- `Separator` — 在 `_run_separation()` 内部导入

避免在模块加载时触发 `soundfile`、`audio-separator`、`torch` 等重型依赖。

---

## 五、测试覆盖

### 5.1 test_plugin_manager.py（9 个测试）

| 测试 | 覆盖内容 |
|------|----------|
| register_and_get | 注册后能获取 |
| get_nonexistent_returns_none | 不存在时返回 None |
| unregister | 注销后获取为 None |
| unregister_nonexistent_is_noop | 注销不存在的不报错 |
| list_plugins | 列出所有插件 |
| execute_injects_rc | 自动注入 RC，插件能读到数据 |
| execute_passes_kwargs | 透传额外参数 |
| execute_nonexistent_raises | 不存在时抛 PluginManagerError |
| register_overwrites | 同名注册覆盖 |

### 5.2 test_analysis_engine.py（9 个测试）

| 测试 | 覆盖内容 |
|------|----------|
| full_pipeline | 完整流水线：节奏→和弦→bass_root，验证 AnalysisResult 各字段 |
| progress_callback | 各阶段均调用回调 |
| no_plugins_still_works | 无插件时不报错，返回默认值 |
| deep_rhythm_trigger_skips_gracefully | complexity > 0.6 时触发但无插件时跳过 |
| result_stored_on_engine | 结果存在 engine.result |
| result_stored_in_rc | 结果存入 RC metadata |
| missing_raw_buffer_raises | 缺 raw buffer 时抛 AnalysisEngineError |
| separation_bridge_writes_stems_to_rc | 分离结果写入 RC buffer |
| test_default_values | AnalysisResult 默认值 |

### 5.3 test_analysis.py 新增（12 个）

| 测试类 | 数量 | 覆盖内容 |
|--------|------|----------|
| TestBuildRhythmInfo | 3 | 包装格式、直接格式、未知格式异常 |
| TestRhythmInfo | 4 | 默认值、beats_per_measure、beat_duration |
| TestRhythmAnalyzer | 5 | dict 分析、华尔兹/平拍推断、无 analyze 时 get_dominant |

### 5.4 汇总

全部 122 个相关测试通过（含 visualizer 下游 34 个 + ResourceController 13 个）。

---

## 六、设计决策记录

### Q1: PluginManager 为什么放在 kernel/core 而不是 plugins？

PluginManager 是 AnalysisEngine 的直接依赖，负责插件的发现和调度。放在 kernel 层符合微内核架构——kernel 管调度，plugins 提供能力。`src/plugins/` 只负责定义 Plugin 基类和具体插件实现。

### Q2: execute() 为什么自动注入 rc？

所有插件都需要 rc 来读写 buffer/metadata。如果让调用方每次手动传 rc，AnalysisEngine 的代码会充斥 `pm.execute(name, rc, **kwargs)`。自动注入简化了调用方代码，也保证了 pm 中注册的插件始终使用同一个 rc 实例。

### Q3: 和弦插件为什么按优先级选择？

目前三个和弦插件（btc_sl、ismir2019、chord_foundation）输出格式相同但模型能力不同。按优先级选择让用户只需注册一个即可工作，也为后续 UI 层的"模型选择"功能预留了扩展点——只需调整优先级列表或让用户指定。

### Q4: _run_separation 为什么用延迟导入？

`separator.py` 在模块顶层 `import soundfile` 和 `from audio_separator.separator import Separator`，这些依赖不在所有环境中都安装。延迟导入确保 AnalysisEngine 在不需要分离功能时（如只做节奏分析测试）不会因缺少 soundfile 而崩溃。

---

## 七、已知限制与后续计划

| 项目 | 当前状态 | 后续 |
|------|----------|------|
| deep_rhythm | 仅预留接口 | Phase C 完成后注册 `rhythm_deep` 插件即可接入 |
| Refiner（和弦纠偏） | analysis_engine.py 中有伪代码函数 | 需封装为独立模块，Level-1（节拍对齐+起音约束）优先 |
| RC 线程安全 | 无锁 | 多插件并发执行时需加锁 |
| VRAM 管理 | 基础 | GPU 模型的加载/卸载需更精细的调度 |
| 进度回调 | 已实现但 UI 未接入 | 前端 WebSocket 进度推送 |
| 完整性验证 | 仅 mock 测试 | 需集成测试：真实音频→分离→和弦→输出 |
