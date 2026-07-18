# 开发日志：Tab3 分析链路全通 — PM 注册 → API → 前端 UI

**日期：** 2026-07-14
**分支：** `feature/p1-ac-pm`
**涉及模块：**
1. `src/kernel/core/plugin_manager.py` — manifest 扫盘扩展
2. `src/kernel/core/kernel_orchestrator.py` — 分析插件列表动态化
3. `src/kernel/core/analysis_engine.py` — `ensure_plugin()` 迁移
4. `src/kernel/kernel.py` — 分析任务 Workshop 集成 + 结果持久化
5. `src/ui/api/analysis.py` — 可视化 / 音频端点真实数据
6. `src/ui/static/index.html` — Tab3 结果面板
7. `src/ui/static/js/app.js` — Tab3 分析配置 + 结果渲染
8. `src/ui/static/css/style.css` — Tab3 样式
9. `src/plugins/rhythm/manifest.json` — 格式修正
10. `src/plugins/rhythm/foundation.py` — 方法调用修正
11. `src/plugins/rhythm/utils.py` — madmom 降级 fallback
12. `src/plugins/chord/chordnet_2e1d.py` — checkpoint 架构推断
13. `src/plugins/chord/ismir2019.py` — 路径 + NumPy 兼容
14. `src/models/__init__.py` — ChordMini 模型桥接（新建）
15. `src/utils/checkpoint_utils.py` — ChordMini checkpoint 桥接（新建）
16. `src/utils/__init__.py` — 导出桥接符号
17. chordmini `common/__init__.py` — 循环导入修复

---

## 一、开发目标

将 Tab3（音轨分析）从"后端代码存在但链路不通"的状态，推进到 **PM manifest 发现 → API 调用 → 插件执行 → 结果持久化 → SSE 推送 → 前端渲染** 全链路可用。

对照 `docs/workshop_user_flow.md` §2.7 和 `docs/plugin_orchestration.md` 的 TODO-PLUGIN-A1（manifest 扫盘接入 `Kernel.list_*_plugins()`）。

---

## 二、架构层改动

### 2.1 PluginManager manifest 扫盘扩展

**改动文件**: `src/kernel/core/plugin_manager.py`

**问题**: `_DEFAULT_MANIFEST_SUBDIRS` 只包含 `("plugins", "separation")`，chord 和 rhythm 的 manifest 从未被发现。

**修复**:
```python
_DEFAULT_MANIFEST_SUBDIRS = (
    ("plugins", "separation"),
    ("plugins", "chord"),
    ("plugins", "rhythm"),
)
```

同时，`refresh_manifests()` 的 glob 模式 `*/manifest.json` 只能匹配 `separation/model_1/manifest.json` 这类子目录布局，chord/rhythm 的 manifest 直接放在插件根目录。新增直接扫描逻辑：

```python
direct = search_dir / "manifest.json"
if direct.is_file():
    self._load_manifest_file(direct)
for manifest_path in sorted(search_dir.glob("*/manifest.json")):
    self._load_manifest_file(manifest_path)
```

**效果**: PM 发现的 manifest 从 1 个（separation）增加到 6 个（+4 chord + 1 rhythm）。

### 2.2 AnalysisEngine 插件查找：`get()` → `ensure_plugin()`

**改动文件**: `src/kernel/core/analysis_engine.py`

**问题**: `_run_rhythm()`、`_run_chord()`、`_run_bass_progression()`、`run_single()` 四个方法都使用 `self._pm.get(name)` 查找插件，但 manifest 扫描到的插件从未被实例化注册，`get()` 永远返回 `None`。

**修复**: 全部改为 `self._pm.ensure_plugin(name)`，利用 PM 的惰性实例化能力（manifest → import → register → return）。

### 2.3 Orchestrator 分析插件列表动态化

**改动文件**: `src/kernel/core/kernel_orchestrator.py`

**问题**: `list_analyzer_plugins()` 硬编码只返回 `example_analyzer`。

**修复**: 改为从 manifest 动态拉取：
```python
def list_analyzer_plugins(self):
    plugins = []
    plugins.extend(self.pm.get_available_plugins(phase="post-separation"))
    plugins.extend(self.pm.get_available_plugins(phase="pre-separation"))
    # example_analyzer 保留为 fallback
    ...
```

### 2.4 `stem_name` 透传到插件

**改动文件**: `src/kernel/core/kernel_orchestrator.py`

**问题**: `Orchestrator.start_analysis()` 接收了 `stem_name` 参数但从未传给插件。`call_plugin_execute_async()` → `plugin.execute()` 的 kwargs 只包含 `durations_sec` 和 `progress_callback`，chord 插件依赖 `kwargs.get("stem_name")` 决定读哪个 RC buffer，不传就永远分析默认的 `"piano"`。

**修复**:
- `call_plugin_execute_async()` 新增 `**extra_kwargs` 参数，合并到传给插件的 kwargs
- `Orchestrator.start_analysis()` 调用时传 `stem_name=stem_name`

---

## 三、Kernel / API 层改动

### 3.1 分析任务 Workshop 集成

**改动文件**: `src/kernel/kernel.py`

**问题**: `start_analysis_task()` 只调 `Orchestrator.start_analysis()` 发射 SSE 事件，完全不与 Workshop 交互——Tab3 的 `state.json` 不会被更新，分析结果不会持久化到 cache。对比 `start_separation_task()` 有完整的 `ws.start_separation()` → `_finalize_separation_task()` → `ws.complete_separation()` 链路。

**修复**: 重构 `start_analysis_task()`，对齐 separation 链路：

1. `ws.upsert_analysis_task(stem_name, plugin_name)` — 标记 running + 写入 state.json
2. `orch.start_analysis(...)` — 执行插件 + SSE 推送
3. `_finalize_analysis_task()` — 等待完成 → 结果持久化到 `cache/analysis_result/` → `ws.complete_analysis()` 标记 done

同时新增 `_load_workshop_stem_into_rc()` — 从 `cache/track_audio/` 加载分离后的音轨到 RC buffer。先检查 RC 是否已有（同 session 分离后），没有再读盘。解决进程重启后 RC 为空导致插件 `get_buffer("piano")` 失败的问题。

### 3.2 分析结果持久化兼容 list 类型

**改动文件**: `src/kernel/kernel.py`

**问题**: chord2e1d / btc_sl 插件返回 `{"data": [list of dicts]}`，`WorkshopCache.save_analysis_result()` 只接受 `dict` 或 `np.ndarray`。

**修复**: `_finalize_analysis_task()` 中检测 `result_data` 为 `list` 时自动包装为 `{"chords": result_data}`。

### 3.3 可视化 API 真实数据

**改动文件**: `src/ui/api/analysis.py`

**问题**: `GET /api/workshops/{wid}/visualization` 返回纯 mock 数据（`_mock_waveform`、`_mock_beats`、`_mock_chords`）。

**修复**: 新增 `_build_waveform()`（从 raw audio 计算波形）、`_extract_visualization_from_tab3()`（读 Tab3 分析结果）、`_build_beats_from_result()` / `_build_chords_from_result()`（从结果 JSON 提取和弦/节拍数据）。mock 函数保留为 fallback。

### 3.4 音频流端点真实数据

**改动文件**: `src/ui/api/analysis.py`

**问题**: `GET /api/workshops/{wid}/audio/{track}` 返回合成测试 wav（5 秒正弦波）。

**修复**: 改为从 `ws.get_track_audio_paths()` 读取真实分离音轨文件，直接 stream 返回。移除 `_generate_test_wav` 死代码。

---

## 四、前端改动

### 4.1 Tab3 分析配置面板

**改动文件**: `src/ui/static/js/app.js`

**问题**: 分离完成后自动跳 Tab3，但界面是空的（`<p class="empty-msg">complete analysis first</p>`）。没有触发分析的 UI。

**修复**:
- `onSeparationDone()` 改为显示 `#phase-analyze`（step-2 的分析配置阶段），加载分析插件列表
- 新增 `renderAnalysisConfig()` — 为 6 轨各渲染一张卡片（插件下拉 + run 按钮 + 状态指示器）
- 新增 `handleRunAnalysis(track)` — 调 `api.analyze()` 启动分析
- 新增 `handleRunAllAnalyses()` — 遍历所有未分析的 track 依次启动
- 新增 `updateAnalysisCardState()` — 更新单轨卡片的 running/done/idle 状态

### 4.2 分析事件处理

**改动文件**: `src/ui/static/js/app.js`

**问题**: 前端只监听 `analysis_started` 和 `analysis_done`，缺少 `analysis_failed` 和 `analysis_progress`。

**修复**:
- 新增 `.on('analysis_progress', ...)` → `onAnalysisProgress()` 在卡片上显示百分比
- 新增 `.on('analysis_failed', ...)` → `onAnalysisFailed()` 重置卡片状态 + toast 报错
- `onAnalysisDone()` 增加 `updateAnalysisCardState(track, 'done')` + `renderAnalysisResults()`

### 4.3 Tab3 结果展示

**改动文件**: `src/ui/static/js/app.js`、`src/ui/static/index.html`、`src/ui/static/css/style.css`

Tab3 面板改为 `#analysis-results-list` 容器。`renderAnalysisResults()` 遍历 `state.analysisResults` 渲染每轨的和弦序列、BPM、调性等信息。

---

## 五、依赖 / 环境修复

### 5.1 ChordMini 子模块初始化

```bash
git submodule update --init src/plugins/chord/external/chordmini
```

子模块此前从未被拉取，`src/models/` 桥接包也没有创建。

### 5.2 ChordMini 模型桥接

**新建文件**:
- `src/models/__init__.py` — 扩展 `__path__` 指向 chordmini 的 `src/models/`，使 `from src.models.btc_model import BTC_model` 可解析
- `src/utils/checkpoint_utils.py` — 从 chordmini `checkpoint_utils.py` 内联 `extract_state_dict_and_stats` 等 3 个函数 + 3 个 logger stub，避免 chordmini 的相对导入在独立加载时失败
- `src/utils/__init__.py` — 导出 `extract_model_state_dict`、`info` 等供 chordmini 的 `checkpoint_loading.py` 使用

**修改文件**:
- chordmini `common/__init__.py` — `load_model` 改为函数内延迟导入，消除 `btc_model` ↔ `common.checkpoint_loading` 之间的循环导入

### 5.3 ChordNet checkpoint 架构推断

**改动文件**: `src/plugins/chord/chordnet_2e1d.py`

**问题**: `2e1d_model_best.pth` 是用 `n_group=2`（d_model=72）训练的，但 `get_chordnet_config()` 默认 `n_group=12`（d_model=12）。`model.load_state_dict(state_dict, strict=False)` 无法处理 shape 不匹配。

错误信息：
```
size mismatch for transformer.encoder_f.0.attn_layer.0.in_proj_weight:
  copying a param with shape torch.Size([216, 72]) from checkpoint,
  the shape in current model is torch.Size([36, 12])
```

**修复**: 重写 `_init_model()`，新增 `_infer_architecture()` — 在创建模型前从 checkpoint state_dict 推断实际参数：
- `n_group`: 从 `encoder_f.0.attn_layer.0.out_proj.weight` 的 shape 反推 `d_model`，`n_group = n_freq / d_model`
- `f_layer / t_layer / d_layer`: 统计 state_dict 中各 encoder/decoder 的 `attn_layer` 索引
- `f_head / t_head / d_head`: 从 `in_proj_weight` 的 embedding dim 推断

### 5.4 ISMIR2019 numpy 兼容性

**改动文件**: `src/plugins/chord/ismir2019.py` 及子模块 4 个文件

**问题 1**: `EXTERNAL_DIR` 是相对路径，`subprocess.run(cmd, cwd=EXTERNAL_DIR)` 导致 `CHORD_PY` 路径双重拼接。

**修复**: `EXTERNAL_DIR` 改为 `os.path.abspath(...)`，`CHORD_PY` 变为绝对路径。

**问题 2**: 子模块代码使用 `np.int`（NumPy 2.x 已移除），17 处报 `AttributeError: module 'numpy' has no attribute 'int'`。

**修复**: `xhmm_ismir.py`、`xhmm_decoder.py`、`beat_preprocess.py`、`results_ismir2017.py` 全部 `np.int` → `int`。

### 5.5 Rhythm 插件修复

**改动文件**:
- `src/plugins/rhythm/manifest.json` — 从单对象格式改为 `{"plugins": [...]}` 数组格式，`entrypoint` 修正为 `src.plugins.rhythm.foundation`
- `src/plugins/rhythm/foundation.py` — 6 个函数调用从 `self._xxx()` 改为直接调用导入的独立函数 `xxx()`（`to_mono`、`extract_band_envelopes`、`estimate_global_bpm`、`build_tempo_map`、`detect_time_signature`、`calculate_band_sync`）
- `src/plugins/rhythm/utils.py` — `extract_band_envelopes()` 新增 `MADMOM_AVAILABLE=False` 时的 fallback 路径（RMS 能量差分替代 STFT Spectral Flux），解决 `name 'Signal' is not defined` 错误

---

## 六、文件变更清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `src/models/__init__.py` | ChordMini 模型命名空间桥接 |
| `src/utils/checkpoint_utils.py` | ChordMini checkpoint 工具桥接 |

### 修改文件

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/kernel/core/plugin_manager.py` | 增强 | manifest 扫描新增 chord/rhythm + 根目录布局 |
| `src/kernel/core/kernel_orchestrator.py` | 增强 | `list_analyzer_plugins()` 动态化；`call_plugin_execute_async` 支持 `**extra_kwargs`；`start_analysis` 透传 `stem_name` |
| `src/kernel/core/analysis_engine.py` | 重构 | 4 个方法 `pm.get()` → `pm.ensure_plugin()` |
| `src/kernel/kernel.py` | 重构 | `start_analysis_task()` Workshop 集成；`_finalize_analysis_task()` 结果持久化；`_load_workshop_stem_into_rc()` 磁盘恢复 |
| `src/ui/api/analysis.py` | 重构 | `/visualization` 真实数据；`/audio/{track}` 真实 wav 流；移除死代码 |
| `src/ui/static/index.html` | 修改 | Tab3 结果面板 |
| `src/ui/static/js/app.js` | 增强 | +7 个 Tab3 函数；+2 个 SSE 事件监听 |
| `src/ui/static/css/style.css` | 增强 | Tab3 分析配置卡片 + 结果卡片样式 |
| `src/plugins/rhythm/manifest.json` | 修正 | 格式对齐 PM 解析规范 |
| `src/plugins/rhythm/foundation.py` | 修正 | 6 个 `self._xxx()` → `xxx()` |
| `src/plugins/rhythm/utils.py` | 增强 | `extract_band_envelopes` fallback |
| `src/plugins/chord/chordnet_2e1d.py` | 重构 | `_init_model()` checkpoint 架构推断 |
| `src/plugins/chord/ismir2019.py` | 修正 | 绝对路径 |
| `src/utils/__init__.py` | 增强 | 导出 chordmini 桥接符号 |
| chordmini `models/common/__init__.py` | 修正 | 循环导入修复 |
| ISMIR2019 子模块 4 文件 | 兼容 | `np.int` → `int` |

---

## 七、验证

### 7.1 后端验证

```powershell
# Manifest 发现
curl http://127.0.0.1:8000/api/plugins/analyzers
# → 6 个插件: chord_chordnet_2e1d, chord_btc_sl, chord_ismir2019,
#   chord_bass_root, rhythm_foundation, example_analyzer

# 车间创建 + 分离 + 分析端到端
# Tab1 上传 → Tab2 分离 → Tab3 分析 → state.json 正确更新
# 结果文件写入 cache/analysis_result/<plugin>_result/result_<task_id>.json
```

### 7.2 编译

```powershell
python -m py_compile src/kernel/kernel.py src/kernel/core/plugin_manager.py `
  src/kernel/core/kernel_orchestrator.py src/kernel/core/analysis_engine.py `
  src/ui/api/analysis.py src/plugins/rhythm/foundation.py `
  src/plugins/rhythm/utils.py src/plugins/chord/chordnet_2e1d.py `
  src/plugins/chord/ismir2019.py
# → 全部通过
```

### 7.3 模型加载

```python
# ChordNet checkpoint 架构推断 + 加载
from src.plugins.chord.chordnet_2e1d import ChordNet2E1DPlugin
# 推断结果: n_group=2, f_head=8, t_head=8, d_head=8
# 模型加载 OK, in_proj_weight shape [216, 72] 匹配

# BTC 模型加载
from src.plugins.chord.btc_sl import BTCSLChordPlugin
# btc_model_best.pth 加载 OK（默认配置匹配）

# ISMIR2019 子进程
# chord_recognition.py 执行成功，return code 0
```

---

## 八、已知限制

| 项目 | 状态 | 说明 |
|------|------|------|
| `audio-separator` | 未安装 | `diffq-fixed` 在 Python 3.14 上编译失败，BS-RoFormer 真实分离不可用。需在 Python 3.11 环境（conda env `pyq`）运行 |
| `madmom` | 未安装 | Windows 编译困难，rhythm 插件使用 RMS fallback，BPM 精度下降 |
| 分析进度事件重复 | 已知 | `analysis_started` / `analysis_failed` 由 Workshop 和 Orchestrator 各发一次，前端处理幂等 |
| Tab3 重启后结果加载 | 部分 | `loadActiveWorkshopData` 只恢复 `_restored` 占位符，不加载实际 chord 数据 |

---

## 九、后续建议

1. 在 Python 3.11 环境（`D:\anaconda\envs\pyq\`）中验证完整的 BS-RoFormer 分离 + 真实和弦分析链路
2. 为 Tab3 重启恢复补充从磁盘加载分析结果 JSON 的逻辑
3. 为 manifest 插件补更细的单测——mock import 一个轻量 manifest 插件，覆盖 `ensure_plugin()` / `execute()` / 结果持久化
