# 开发日志：分离插件架构对齐 — model_1 插件化改造


该分支继承的ui-layer
该分支继承的ui-layer
该分支继承的ui-layer


**日期：** 2026-07-10
**涉及模块：**
1. `src/plugins/separation/model_1/separator.py`（新建）
2. `src/plugins/separation/model_1/manifest.json`（新建）
3. `src/plugins/separation/model_1/__init__.py`（新建）
**参考文件（未修改）：** `src/plugins/separation/separator.py`

---

## 一、改造目标

将 `src/plugins/separation/separator.py` 中的独立 `Separator` 服务类，按照 TABsucks 插件架构规范（参考 chord/rhythm 插件格式）转换为标准插件形态，写入 `model_1/` 目录，**不修改原始文件**。

### 改造前 vs 改造后

| 维度 | 原始 `separator.py` | 新版 `model_1/separator.py` |
|------|---------------------|---------------------------|
| 架构 | 独立服务类，直接接收 `AudioData` | 继承 `BasePlugin`，遵循插件架构 |
| 对外接口 | `separate(audio: AudioData) -> SeparationResult` | `execute(rc: ResourceController, **kwargs) -> dict` |
| 输入方式 | 调用方直接传入 `AudioData` 对象 | 从 `rc.get_buffer("raw")` + `rc.get_metadata("sample_rate")` 获取 |
| 输出方式 | 返回 `SeparationResult` 给调用方自行处理 | 通过 `rc.set_buffer(stem_name, data)` 回写各 stem；返回规范化状态字典 |
| 注册方式 | 无 | `manifest.json` 注册（对齐 chord 插件 `{"plugins": [...]}` 格式） |

---

## 二、核心设计决策

### 2.1 插件类命名

- 新类名 `SeparationPlugin`，区别于原始的 `Separator`
- `name` 属性返回 `"separation_bs_roformer"`，与 manifest 中的 `name` 字段一致
- `version` 属性返回 `"1.0.0"`

### 2.2 ResourceController 集成

原始代码的 `separate()` 方法签名从：

```python
def separate(self, audio: AudioData) -> SeparationResult
```

改为插件标准的 `execute()`：

```python
def execute(self, rc: ResourceController, **kwargs) -> dict[str, Any]
```

内部逻辑：
1. `rc.get_buffer("raw")` → 获取原始混音 numpy 数组
2. `rc.get_metadata("sample_rate")` → 获取采样率
3. 执行 6 轨分离（核心算法不变）
4. `rc.set_buffer(track_id.value, stem_data)` → 逐轨回写
5. `rc.set_metadata(...)` → 写入分离模型名、stem 列表等元数据
6. `return {"status": "success", "data": {...}}` → 规范化返回

### 2.3 Manifest 格式选择

采用 chord 插件的 `{"plugins": [...]}` 数组格式，而非 rhythm 的单对象格式。原因：
- 数组格式支持未来在同一 manifest 下注册多个分离模型变体
- 结构更详细，包含 `input_stems`、`output`、`requirements` 等下游插件需要的字段
- 与已有 chord manifest 保持一致的 `phase` 字段，标记为 `"separation"` 阶段

### 2.4 保留的核心逻辑

以下原始逻辑完整保留，未做任何修改：
- BS-RoFormer 模型加载（延迟初始化 `_init_engine()`）
- 临时文件写入 → 模型推理 → 结果文件读取 → 清理流程
- 6 轨文件名关键词归类（vocals/drums/bass/piano/guitar/other）
- 全零数组兜底（防止模型漏轨导致下游崩溃）
- 采样对齐逻辑（兼容 1D/2D 数组的截断与补零）
- `TrackId` 枚举、`SeparationResult` 数据类、`SeparatorError` 异常类
- `separate_file()` 快捷方法（适配为接受 `ResourceController` 参数）

---

## 三、文件清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `src/plugins/separation/model_1/separator.py` | 插件实现：`SeparationPlugin(BasePlugin)`，核心分离逻辑保持不变 |
| `src/plugins/separation/model_1/manifest.json` | 插件清单：`separation_bs_roformer`，phase=`separation`，6 轨输出 |
| `src/plugins/separation/model_1/__init__.py` | 包声明，导出 `SeparationPlugin`、`SeparationResult`、`SeparatorError` |

### 未修改文件

| 文件 | 说明 |
|------|------|
| `src/plugins/separation/separator.py` | 原始独立服务类，保持原样不动 |

---

## 四、Manifest 注册信息

```json
{
  "plugins": [
    {
      "name": "separation_bs_roformer",
      "class": "SeparationPlugin",
      "entrypoint": "src.plugins.separation.model_1.separator",
      "display_name": "BS-RoFormer 6-Stem Audio Separator",
      "version": "1.0.0",
      "input_stems": [],
      "output": "separated_stems",
      "requirements": {
        "gpu_memory_mb": 4096,
        "ram_mb_min": 2048,
        "python_packages": ["torch", "numpy", "soundfile", "audio-separator", "onnxruntime-gpu"]
      },
      "phase": "separation"
    }
  ]
}
```

- `input_stems` 为空数组，因为分离插件运行在原始混音上，不依赖其他 stem
- `phase: "separation"` 标识该插件在分离阶段执行，为下游 `post-separation` 插件（和弦识别等）提供输入
- GPU 内存要求 4096 MB，对应 BS-RoFormer 模型的典型推理开销

---

## 五、与现有架构的关系

```
分析流水线中的位置：

  [原始音频] ──→ SeparationPlugin (model_1) ──→ 6 个 stem buffer
                     │                              │
                     │ phase: separation             ▼
                     │                    post-separation 插件
                     │                    (chord_btc_sl, chord_chordnet_2e1d, ...)
                     │
              原始 separator.py 保持不变
              (未来可能作为备选实现或直接淘汰)
```

下游 post-separation 插件（如 `BTCSLChordPlugin`）通过 `rc.get_buffer("piano")` 等方式获取分离后的音轨数据，与 `SeparationPlugin` 通过 `rc.set_buffer()` 写入的键名完全对应。

---

## 六、后续待办

1. **PluginManager 集成**：将 `model_1/manifest.json` 注册到插件的自动发现/加载机制中
2. **UI Mock 替换**：对接 `src/ui/api/analysis.py` 中的 `_run_mock_separation()` → 真实 `SeparationPlugin.execute()`
3. **原始 separator.py 去留**：确认无其他模块依赖原始 `Separator` 类后，可考虑废弃或归档
4. **多模型扩展**：如未来接入其他分离模型（如 Demucs），只需在 `model_1/` 同级新建 `model_2/`，遵循相同的 manifest + plugin 格式即可

---

# 开发日志：SeparationPluginManager — 分离插件管理器

**日期：** 2026-07-10
**涉及模块：**
1. `src/kernel/core/plugin_manager_s.py`（新建）
**参考文件（未修改）：** `src/kernel/core/plugin_manager.py`、`src/kernel/core/resource_controller.py`、`src/kernel/core/analysis_engine.py`

---

## 一、改造目标

在现有 `PluginManager` 基类之上，构建音轨分离专用的 `SeparationPluginManager`，为 UI 和 AnalysisEngine 之间的协作补齐 **manifest 发现 → 动态实例化 → 硬件探针 → VRAM 准备 → 执行** 的完整链路。

### 与用户原始设想的对照与适配

用户原始设想了一个理想化流程。在实现时，以现有代码库架构为基准做了以下适配：

| 用户设想 | 现有代码库约束 | 实际实现 |
|----------|--------------|---------|
| `BSRoformerPlugin.check_compatibility()` 挂在插件实例上 | `Plugin` 基类无此方法，且本次只能改 `plugin_manager_s.py` | 作为 `SeparationPluginManager.check_compatibility(name)` 实现，通过插件名查询 manifest 中的硬件要求 |
| `rc.allocate_vram(self)` 挂在 ResourceController 上 | `ResourceController` 无显存管理接口，本次不能改 | 作为 `SeparationPluginManager.prepare_vram(name)` 实现，内部调用 `rc.release_all_models()` 清场 |
| 插件名用反向域名 `com.tabsucks.sep.bsroformer` | 现有插件全部用简单字符串 `chord_btc_sl`、`rhythm_foundation` | 对齐现有约定，使用 manifest 中的 `name` 字段 `"separation_bs_roformer"` |
| "动态编译" | Python 没有编译步骤 | 改为 `importlib.import_module(entrypoint)` 动态导入 + 反射获取类 |

---

## 二、架构设计

### 2.1 继承关系

```
PluginManager                    (src/kernel/core/plugin_manager.py)
    │
    ├── register(plugin)         # 注册插件实例
    ├── execute(name, **kwargs)  # 执行插件
    ├── get(name)                # 按名查找
    └── list_plugins()           # 列出已注册插件
         │
         └── SeparationPluginManager   (src/kernel/core/plugin_manager_s.py)
              │
              ├── _discover()              # 扫描 model_*/manifest.json
              ├── get_available_plugins()  # 返回 manifest 元数据列表（供 UI）
              ├── get_manifest(name)       # 查询单个 manifest
              ├── instantiate_plugin()     # 动态导入 + 实例化 + 注册
              ├── check_compatibility()    # GPU/RAM/依赖包 探针
              ├── prepare_vram()           # 释放缓存模型 + 显存检查
              └── refresh_manifests()      # 热刷新 manifest 注册表
```

### 2.2 数据流

```
┌─────────────────────────────────────────────────────────────────┐
│ UI 层                                                           │
│   pm.get_available_plugins()                                    │
│   → [{name, display_name, version, requirements, ...}, ...]     │
│   渲染下拉菜单，用户选择插件 + 配置参数                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 用户选择: {name, model_name, ...}
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ AnalysisEngine                                                  │
│                                                                 │
│   1. pm.instantiate_plugin(name, config)                        │
│      → importlib.import_module(entrypoint)                      │
│      → getattr(module, class_name)                              │
│      → plugin_cls(**config)                                     │
│      → pm.register(instance)                                    │
│                                                                 │
│   2. compat = pm.check_compatibility(name)                      │
│      → GPU 显存探针 (torch.cuda.mem_get_info)                    │
│      → RAM 探针 (psutil, 可选)                                   │
│      → Python 包检查 (importlib)                                 │
│      → 返回 {compatible, warnings, ...}                         │
│                                                                 │
│   3. vram = pm.prepare_vram(name)                               │
│      → rc.release_all_models()   # 清空其他模型                  │
│      → GPU 空闲显存对比 manifest 阈值                             │
│      → 返回 {ready, message, ...}                               │
│                                                                 │
│   4. result = pm.execute(name)                                  │
│      → plugin.execute(rc)                                       │
│      → 插件内部: rc.get_buffer("raw") → 分离 → rc.set_buffer()   │
│                                                                 │
│   5. 下游插件通过 rc.get_buffer("piano") 等读取分离结果           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、核心实现细节

### 3.1 Manifest 自动发现 (`_discover`)

- 从 `plugin_manager_s.py` 的文件位置反推项目 `src/` 目录（`Path(__file__).resolve().parents[2]`）
- 在 `src/plugins/separation/model_*/manifest.json` 下搜索
- 解析 `{"plugins": [...]}` 数组格式（对齐 chord manifest）
- 每个条目以 `name` 为键存入 `_manifests`，附加 `_manifest_dir` 字段记录所在目录

### 3.2 动态实例化 (`instantiate_plugin`)

```
instantiate_plugin(name, config)
  │
  ├─ 1. 查 manifest → 取 entrypoint + class
  ├─ 2. importlib.import_module(entrypoint)   # 动态导入
  ├─ 3. getattr(module, class_name)            # 获取插件类
  ├─ 4. plugin_cls(**config)                   # 实例化
  └─ 5. self.register(instance)                # 注册到父类
```

- `config` 直接传给插件类的 `__init__`（如 `SeparationPlugin(model_name="BS-Roformer-SW.ckpt")`）
- 实例化后自动注册到父类 `PluginManager._plugins`，后续 `execute()` 可直接使用
- 所有异常统一包装为 `SeparationPluginManagerError`

### 3.3 硬件兼容性探针 (`check_compatibility`)

三个检查维度：

| 维度 | 实现方式 | 失败策略 |
|------|---------|---------|
| GPU 显存 | `torch.cuda.mem_get_info()[0]` / 1024² → 空闲 MB，与 `gpu_memory_mb` 阈值对比 | 不足时 `gpu_ok=False` + warning；CUDA 不可用时仅 warning |
| 系统 RAM | `psutil.virtual_memory().available` → 对比 `ram_mb_min` | psutil 未安装时静默跳过 |
| Python 包 | `importlib.import_module()` 逐个尝试 | 缺失时记入 `missing_packages` + warning |

包名映射表处理 pip 名与 import 名不一致的情况（如 `audio-separator` → `audio_separator`、`onnxruntime-gpu` → `onnxruntime`）。

### 3.4 VRAM 准备 (`prepare_vram`)

核心逻辑：
1. **清场**：`rc.release_all_models()` 释放所有已缓存模型（清空 `rc._models`），Python GC 随后回收显存
2. **探测**：`torch.cuda.mem_get_info()` 获取空闲显存
3. **对比**：与 manifest 中 `gpu_memory_mb` 阈值比较
4. **返回**：`{ready: bool, message: str, ...}` 供 AnalysisEngine 判断是否继续

这是防止 OOM 的关键步骤 —— 在加载重量级分离模型前先腾出显存空间。

### 3.5 与现有 PluginManager 的兼容性

`SeparationPluginManager` 完全兼容现有 `PluginManager` 的全部接口：
- 已有的 `register()` / `execute()` / `get()` / `list_plugins()` 不受影响
- `instantiate_plugin()` 内部调用 `register()`，与手动 `register()` 混用无冲突
- `execute()` 继承自父类，自动注入 `ResourceController`

---

## 四、与现有 AnalysisEngine 的集成点

当前 `AnalysisEngine._run_separation()` 直接硬编码导入旧 `Separator`：

```python
from src.plugins.separation.separator import Separator, TrackId
separator = Separator()
result = separator.separate(audio_data)
```

替换为 `SeparationPluginManager` 后的调用方式：

```python
# 注册阶段（AnalysisEngine.__init__ 或 setup）
pm = SeparationPluginManager(rc)

# 运行阶段（_run_separation 替换实现）
compat = pm.check_compatibility("separation_bs_roformer")
if not compat["compatible"]:
    raise AnalysisEngineError(f"硬件不兼容: {compat['warnings']}")

pm.prepare_vram("separation_bs_roformer")
pm.instantiate_plugin("separation_bs_roformer")
result = pm.execute("separation_bs_roformer")
# 插件内部已调用 rc.set_buffer() 回写各 stem，无需手动桥接
```

**优势**：
- AnalysisEngine 不再硬编码 `Separator` 导入，通过 manifest 解耦
- 新增分离模型只需加一个 `model_2/manifest.json` + 对应插件类
- 兼容性检查统一在 manager 层处理，不侵入引擎代码

---

## 五、文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/kernel/core/plugin_manager_s.py` | ~280 | `SeparationPluginManager(PluginManager)`，manifest 发现 + 动态实例化 + 硬件探针 + VRAM 管理 |

### 未修改文件

| 文件 | 说明 |
|------|------|
| `src/kernel/core/plugin_manager.py` | 基类，保持不变 |
| `src/kernel/core/resource_controller.py` | 不变（`prepare_vram` 通过 `release_all_models()` 间接使用已有接口）|
| `src/kernel/core/analysis_engine.py` | 暂不改动，后续集成时替换 `_run_separation()` |
| `src/plugins/__init__.py` | Plugin 基类，保持不变 |

---

## 六、后续待办

1. **AnalysisEngine 集成**：将 `_run_separation()` 从硬编码 `Separator` 切换为通过 `SeparationPluginManager` 调度
2. **UI 对接**：`get_available_plugins()` 的返回值对接前端下拉菜单渲染
3. **check_compatibility 缓存**：同一会话内对同一硬件配置的检查结果可缓存，避免重复探针
4. **显存分配器**：后续可升级 `prepare_vram` 为更精细的显存预算系统（注册→申请→释放），支持多插件排队使用 GPU
5. **torch 空缓存**：在 `prepare_vram` 中可增加 `torch.cuda.empty_cache()` 调用以更彻底地回收显存碎片

---

# 开发日志：ResourceController_s — 带显存管理与线程安全的资源控制器

**日期：** 2026-07-10
**涉及模块：**
1. `src/kernel/core/resource_controller_s.py`（新建）
**参考文件（未修改）：** `src/kernel/core/resource_controller.py`、`src/kernel/core/plugin_manager_s.py`

---

## 一、改造目标

现有 `ResourceController` 提供了基础的 buffer / metadata / model 管理，但缺少两个关键能力：

1. **线程安全**：用户原始设想中明确要求"带有互斥锁的共享字典"。当前 `_buffers` 和 `_metadata` 是裸 `dict`，在多线程场景（UI 轮询 + 分离线程回写）下存在竞态风险。
2. **显存管理**：用户原始设想中的 `allocate_vram(self)`——在执行重量级分离前"清场"并校验显存余量。当前 RC 仅有 `release_all_models()`，没有申请/批准/配额的完整流程。

`ResourceController_s` 继承 `ResourceController`，补齐上述两个能力。

### 与用户原始设想的对照

| 用户设想 | 实现 |
|----------|------|
| "带有互斥锁的共享字典" | 所有 `get_buffer`/`set_buffer`/`get_metadata`/`set_metadata` 覆盖为 `with self._lock:` 保护 |
| `rc.allocate_vram(self)` | `rc.allocate_vram(requester, amount_mb, auto_release=True)` — 清场 + 探测 + 批准/拒绝 |
| 防止 OOM | `auto_release=True` 默认先清空所有缓存模型 + `torch.cuda.empty_cache()` 回收碎片 |
| 分析后释放 | `rc.release_vram(requester)` — 归还配额 + `empty_cache()` |
| 批量写入 6 轨 | `rc.set_buffers_batch({...})` — 一次锁内完成全部回写 |

---

## 二、架构设计

### 2.1 继承关系

```
ResourceController              (src/kernel/core/resource_controller.py)
    │
    ├── get_buffer / set_buffer   # 基础读写
    ├── get_metadata / set_metadata
    ├── get_current_device / request_model / release_model / release_all_models
    └── clear()
         │
         └── ResourceController_s   (src/kernel/core/resource_controller_s.py)
              │
              ├── (覆盖) 所有读写方法 → 加 threading.RLock
              ├── set_buffers_batch()    # 批量写入（一次锁）
              ├── get_buffers_batch()    # 批量读取（一次锁）
              ├── get_gpu_info()         # GPU 探针（静态方法）
              ├── allocate_vram()        # 显存申请（清场 + 校验 + 批准）
              ├── release_vram()         # 显存释放
              ├── release_all_vram()     # 全部释放
              └── vram_status            # 分配状态快照 (property)
```

### 2.2 线程安全设计

使用 `threading.RLock`（可重入锁）而非 `Lock`：

- **可重入**：同一线程内 `set_buffers_batch()` 内部调用 `set_buffer()` 不会死锁
- **读写同一把锁**：简化设计，避免读写锁的复杂度。音频分析场景下写操作（分离回写）频率低、耗时短，不会成为瓶颈

覆盖的 6 个基类方法全部加锁：

```python
def set_buffer(self, name, data):
    with self._lock:
        super().set_buffer(name, data)
```

新增的批量操作允许在一次锁持有期间完成多个读写：

```python
# 分离完成后一次性回写 6 轨（1 次锁 vs 6 次锁）
rc.set_buffers_batch({
    "vocals": vocals_data,
    "drums": drums_data,
    "bass": bass_data,
    "piano": piano_data,
    "guitar": guitar_data,
    "other": other_data,
})
```

### 2.3 显存分配流程

```
allocate_vram(requester="separation_bs_roformer", amount_mb=4096)
  │
  ├─ 1. auto_release=True?
  │      └─ YES → self._models.clear()         # 清空所有缓存模型
  │              + torch.cuda.empty_cache()     # 回收 PyTorch 缓存碎片
  │
  ├─ 2. get_gpu_info() → {cuda_available, free_mb, total_mb, ...}
  │
  ├─ 3. CUDA 不可用?
  │      └─ YES → granted=True（CPU 模式，不阻塞）
  │
  ├─ 4. free_mb < amount_mb?
  │      └─ YES → granted=False + 详细拒绝原因
  │
  └─ 5. 批准 → 登记 _vram_allocations[requester] = amount_mb
               → granted=True + 空闲/总量信息
```

### 2.4 GPU 探针 (`get_gpu_info`)

静态方法，不依赖实例状态，供任何模块调用：

```python
>>> rc.get_gpu_info()
{
    "cuda_available": True,
    "device_count": 1,
    "device_name": "NVIDIA GeForce RTX 3060",
    "free_mb": 8192.0,
    "total_mb": 12288.0,
    "used_mb": 4096.0,
}
```

---

## 三、与 SeparationPluginManager 的关系

两个类各司其职：

```
SeparationPluginManager          ResourceController_s
─────────────────────────        ─────────────────────────
manifest 发现                    buffer / metadata 存储
动态实例化                       GPU 探针
硬件兼容性检查                   VRAM 配额管理 (allocate/release)
插件注册与调度                   模型生命周期
                                线程安全
```

`SeparationPluginManager` 的方法在需要显存操作时，内部委托给 `ResourceController_s`：

| PM 方法 | 使用的 RC_s 能力 |
|---------|-----------------|
| `check_compatibility()` | `get_gpu_info()` — 获取空闲显存 |
| `prepare_vram()` | `release_all_models()` + `get_gpu_info()` |

后续可进一步将 `prepare_vram()` 的实现改为调用 `rc.allocate_vram()` 以复用配额管理。

---

## 四、完整协作流程（三者串联）

以一次完整的分离任务为例：

```python
rc = ResourceController_s()
pm = SeparationPluginManager(rc)

# ── UI 阶段 ──
# 前端调用 get_available_plugins() 渲染下拉菜单
plugins = pm.get_available_plugins()
# → [{name: "separation_bs_roformer", display_name: "BS-RoFormer ...", ...}]

# ── 实例化阶段 ──
# 用户选择插件 + model_name，AnalysisEngine 动态实例化
pm.instantiate_plugin("separation_bs_roformer",
                      {"model_name": "BS-Roformer-SW.ckpt"})

# ── 环境检查阶段 ──
compat = pm.check_compatibility("separation_bs_roformer")
# → {compatible: True, gpu_free_mb: 8192, warnings: [], ...}

# ── 显存申请（最核心一步）──
alloc = rc.allocate_vram("separation_bs_roformer", amount_mb=4096)
if not alloc["granted"]:
    raise ResourceControllerError(alloc["message"])
# → 内部: release_all_models() + empty_cache() + 探测 + 批准
# → {granted: True, free_mb: 8192, message: "显存已分配: 4096 MB..."}

# ── 数据注入（线程安全）──
rc.set_buffer("raw", audio_array)
rc.set_metadata("sample_rate", 44100)

# ── 执行分离 ──
result = pm.execute("separation_bs_roformer")
# 插件内部: raw = rc.get_buffer("raw")  ← 线程安全读
#          ... BS-RoFormer 推理 ...
#          rc.set_buffers_batch({...})   ← 线程安全批量写

# ── 下游消费 ──
piano = rc.get_buffer("piano")  # 和弦分析插件读取
drums = rc.get_buffer("drums")  # 节奏分析插件读取

# ── 显存释放 ──
rc.release_vram("separation_bs_roformer")
# → {released: True, message: "...4096 MB 显存配额已释放"}

# 查看当前状态
print(rc.vram_status)
# → {allocations: {}, total_allocated_mb: 0, gpu: {...}}
```

---

## 五、文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/kernel/core/resource_controller_s.py` | ~300 | `ResourceController_s(ResourceController)`，线程安全 + 显存分配器 + GPU 探针 |

### 未修改文件

| 文件 | 说明 |
|------|------|
| `src/kernel/core/resource_controller.py` | 基类，保持不变 |
| `src/kernel/core/plugin_manager_s.py` | 保持不变（后续可将 `prepare_vram` 内部改为委托 `rc.allocate_vram()`） |
| `src/kernel/core/plugin_manager.py` | 保持不变 |

---

## 六、后续待办

1. **`prepare_vram` 重构**：将 `SeparationPluginManager.prepare_vram()` 内部改为调用 `rc.allocate_vram()`，消除 GPU 探针的代码重复
2. **显存预算系统**：当前一个 requester 可以重复申请（累加配额），后续可改为预算上限 + 申请/释放配对的严格模式
3. **多 GPU 支持**：`get_gpu_info()` 和 `allocate_vram()` 当前仅探测 GPU 0，多卡场景需扩展
4. **监控指标导出**：`vram_status` 可对接 UI 的状态栏，实时显示显存占用

---

# 2026-07-13 追加日志：合并 `_s` 分支实现并接通 Orchestrator / AnalysisEngine / Tab2

## 一、架构选择

本次在两个方案之间选择了 **方案 1**：

1. 将 `plugin_manager_s.py`、`resource_controller_s.py` 的真实能力合并回非 `_s` 版本；
2. `_s` 文件只保留兼容 wrapper，避免旧 import 立即失效；
3. 以合并后的 `PluginManager` / `ResourceController` 作为唯一 canonical 实现；
4. 再基于 canonical PM/RC 完善 `Orchestrator`、`AnalysisEngine` 和 Tab2 的真实执行链路。

选择理由：

- `_s` 继续独立存在会让 PM/RC 双轨演化，后续 `Orchestrator` 和 `AnalysisEngine` 需要不断判断该依赖哪一套接口。
- 合并后，manifest 扫描、动态实例化、硬件兼容检查、VRAM 预算、buffer/metadata 管理都集中在主实现中。
- `_s` wrapper 可以保护当前分支已有调用方式，迁移成本低。

## 二、本次修改文件

| 文件 | 本次状态 | 说明 |
|---|---|---|
| `src/kernel/core/resource_controller.py` | 重写/增强 | 成为 canonical ResourceController，包含线程锁、批量 buffer API、GPU 探针、VRAM allocate/release/status。 |
| `src/kernel/core/resource_controller_s.py` | 改为兼容 wrapper | `ResourceController_s(ResourceController)`，旧 import 仍可用。 |
| `src/kernel/core/plugin_manager.py` | 重写/增强 | 成为 canonical PluginManager，保留 register/get/list/execute，并加入 manifest discovery、`ensure_plugin()`、`check_compatibility()`、`prepare_vram()`。 |
| `src/kernel/core/plugin_manager_s.py` | 改为兼容 wrapper | `SeparationPluginManager(PluginManager)`，`SeparationPluginManagerError = PluginManagerError`。 |
| `src/kernel/core/kernel_orchestrator.py` | 完善 | 注册 example 插件，列出 manifest 分离插件，惰性实例化真实插件，执行前准备 VRAM，执行后释放 VRAM。 |
| `src/kernel/core/analysis_engine.py` | 完善 | `_run_separation()` 优先走 `PluginManager.ensure_plugin("separation_bs_roformer")`，失败时回退旧 `Separator`。 |
| `src/kernel/kernel.py` | 完善 Tab2 桥接 | `start_separation_task()` 现在会把 workshop raw audio 加载到 RC；分离完成后把 RC stem buffers 保存为 wav，并写回 Tab2 state。 |
| `tests/unit/test_kernel.py` | 增加测试 | 覆盖 `Kernel.start_separation_task()` 分离后写回 Tab2 tracks 的行为。 |

## 三、PluginManager 合并结果

`src/kernel/core/plugin_manager.py` 现在承担原 `SeparationPluginManager` 的职责：

- 扫描 `src/plugins/separation/model_*/manifest.json`
- 暴露 `get_available_plugins(phase="separation")`
- 读取 `get_manifest(name)`
- 通过 manifest 的 `entrypoint` + `class` 做 `instantiate_plugin(name, config=None)`
- `ensure_plugin(name)`：已注册则返回实例；未注册但有 manifest 则惰性实例化
- `check_compatibility(name)`：检查 GPU 信息、RAM、Python package
- `prepare_vram(name)`：委托 `ResourceController.allocate_vram()`

为了避免启动时卡住，真实 BS-RoFormer 插件不会在 `PluginManager` 初始化时 import；只有用户实际执行 `separation_bs_roformer` 时才会 import entrypoint 并初始化插件实例。

保留的兼容写法：

```python
from src.kernel.core.plugin_manager_s import SeparationPluginManager

pm = SeparationPluginManager(rc)
```

内部实际等价于：

```python
from src.kernel.core.plugin_manager import PluginManager

pm = PluginManager(rc)
```

## 四、ResourceController 合并结果

`src/kernel/core/resource_controller.py` 现在承担原 `ResourceController_s` 的职责：

- `threading.RLock()` 保护 buffer、metadata、model cache、VRAM allocation 状态
- `set_buffers_batch()` / `get_buffers_batch()` 支持批量读写
- `get_gpu_info()` 返回 CUDA 可用性、显存空闲量、总量、设备名等
- `allocate_vram(requester, amount_mb, auto_release=True)` 做 advisory 预算申请
- `release_vram(requester)` / `release_all_vram()` 释放预算
- `vram_status` 暴露当前显存预算状态

保留的兼容写法：

```python
from src.kernel.core.resource_controller_s import ResourceController_s

rc = ResourceController_s()
```

内部实际继承 canonical `ResourceController`。

## 五、Orchestrator 接入结果

`Orchestrator` 当前职责：

- 初始化共享 `ResourceController` 和 `PluginManager`
- 注册 `example_separator` / `example_analyzer`，保留 MVP fallback
- `list_separator_plugins()` 返回 manifest-backed 分离插件 + example fallback
- 支持旧 UI label alias：`BS-RoFormer`、`BS-RoFormer-SW`、`BS-Roformer-SW`、`BS-Roformer-SW.ckpt`、`BS-Roformer-SW.yaml`
- 对 manifest 插件执行：`pm.ensure_plugin()` → `pm.prepare_vram()` → `call_plugin_execute_async()` → `rc.release_vram()` in `finally`

当前 `Orchestrator().list_separator_plugins()` 手动验证返回：

```text
['separation_bs_roformer', 'example_separator']
```

## 六、AnalysisEngine 接入结果

`AnalysisEngine._run_separation()` 已从硬编码旧 `Separator` 转为优先使用 canonical PM：

```python
plugin_name = "separation_bs_roformer"
plugin = self._pm.ensure_plugin(plugin_name)
```

执行路径：

1. 如果 manifest 插件可用：准备 VRAM → `pm.execute("separation_bs_roformer")` → 写入 `separation_plugin_result` metadata → 释放 VRAM。
2. 如果插件不可用：回退到旧 `src.plugins.separation.separator.Separator`，保持现有测试和无重依赖环境可运行。

这样 `AnalysisEngine` 不再把分离实现固定到单个类，而是通过 PM 调度插件。

## 七、Tab2 / Kernel 桥接补齐

本次发现并修复了一个 Tab2 集成缺口：

> 上传接口只把 raw audio 写入 workshop cache/state，但启动分离时没有把该文件解码进 `ResourceController.raw`。

`Kernel.start_separation_task()` 现在补齐完整链路：

1. 校验 workshop 存在；
2. 如果调用方传入 `audio_samples`，直接写入 `orch.rc["raw"]`；
3. 如果没有传入 `audio_samples`，从 `ws.get_raw_audio_path()` 读取 workshop 中已上传的音频；
4. 调用 `src.audio.loader.load_audio()` 解码并写入 RC；
5. 调 `ws.start_separation(plugin_name)` 标记 Tab2 running；
6. 启动 `Orchestrator.start_separation()`；
7. 任务完成后，从 RC 读取 stems；
8. 将 stems 保存为 `track_audio/track_<stem>/<plugin>_<stem>.wav`；
9. 调 `ws.complete_separation(track_files)` 写回 Tab2 state；
10. 如果失败，调 `ws.fail_separation(error)`。

同时补了 stem 保存前的形状归一化：

- 项目 `AudioData/save_audio()` 约定多声道是 `(channels, samples)`
- 真实 `soundfile.read()` 常见输出是 `(samples, channels)`
- 保存前会把明显的 `(samples, channels)` 转成 `(channels, samples)`，避免真实插件输出 wav 维度错乱

## 八、测试与验证

使用解释器：

```powershell
D:\anaconda\envs\pyq\python.exe
```

通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m py_compile `
  src\kernel\kernel.py `
  src\kernel\core\resource_controller.py `
  src\kernel\core\plugin_manager.py `
  src\kernel\core\resource_controller_s.py `
  src\kernel\core\plugin_manager_s.py `
  src\kernel\core\kernel_orchestrator.py `
  src\kernel\core\analysis_engine.py `
  tests\unit\test_kernel.py
```

通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_resource_controller.py `
  tests/unit/test_plugin_manager.py `
  tests/unit/test_kernel_orchestration.py `
  tests/unit/test_analysis_engine.py `
  -q
```

结果：

```text
48 passed
```

手动验证 Tab2 写回链路：

```text
done
['bass', 'drums', 'guitar', 'other', 'piano', 'vocals']
True
```

含义：

- workshop Tab2 状态为 `done`
- 6 个 stem 名称已写入 `TrackAudioFilePath`
- 对应 wav 文件真实存在

## 九、当前环境限制

- `tests/unit/test_http_server.py` 当前环境缺少 `fastapi`，无法收集。
- pytest 使用 `tmp_path` / `--basetemp` 时，Windows 当前环境对临时目录返回 `PermissionError: [WinError 5] 拒绝访问`。
- 本次尝试创建的 `.pytest_tmp` 目录因权限问题无法删除，属于测试环境残留，不是业务代码产物。
- pytest cache 写入 `.pytest_cache` 也有权限 warning，不影响上述 48 个核心测试通过。

## 十、后续建议

1. 继续完成 Tab2 前端 stem grid 渲染：后端已经会写回 `TrackAudioFilePath`，UI 需要在 `separation_done` 后刷新并展示 6 轨。
2. 为 manifest 插件补更细的单测：mock import 一个轻量 manifest 插件，覆盖 `ensure_plugin()` / `prepare_vram()` / `execute()`。
3. 决定 UI 默认选项顺序：当前分离插件列表是 `separation_bs_roformer` 在前，`example_separator` fallback 在后。
4. 后续如果环境补齐 `fastapi`，再跑 HTTP API 测试，重点看 `/api/plugins/separators` 和 `/api/workshops/{wid}/separate`。

---

## 十一、2026-07-13 追加：Tab1 -> Tab2 raw audio 丢失问题的临时 debug 探针

### 现象

真实 UI 流程：

```powershell
python -m src.ui
```

操作：

1. Tab1 正常选歌并上传；
2. 进入 Tab2；
3. 选择分离模型；
4. 点击开始分离；
5. 后端报错：

```text
RuntimeError: Workshop <wid> has no raw audio
```

已观察到一个矛盾点：

- 磁盘 `cache/workshop_<wid>/state.json` 中存在 `TabState.Tab1.RawAudioFilePath`
- 对应 `raw_audio/<filename>` 文件也存在
- 但运行时 `Kernel._load_workshop_raw_audio_into_rc()` 中 `ws.get_raw_audio_path()` 返回 `None`

因此当前优先怀疑：

1. upload 和 separate 请求可能命中了不同的 Kernel/app 实例；
2. 运行时内存中的 `MusicWorkshop.state` 与磁盘 `state.json` 脱节；
3. 前端传给 `/separate` 的 `wid` 不是刚刚上传音频的那个 workshop；
4. `Kernel.start_separation_task()` 过度信任内存 state，缺少从磁盘 state fallback 的兜底。

### 本次临时改动

为了避免再次用长链路脚本卡住，本次只加入轻量状态日志，不执行额外分离脚本。

统一前缀：

```text
[DEBUG-TAB2RAW]
```

新增探针位置：

| 文件 | 位置 | label | 目的 |
|---|---|---|---|
| `src/ui/api/analysis.py` | 文件上传成功后 | `after-upload-file` | 确认 upload 后内存 raw、磁盘 raw、绝对路径是否存在。 |
| `src/ui/api/analysis.py` | URL 上传成功后 | `after-upload-url` | 同上，覆盖 URL 路径。 |
| `src/ui/api/analysis.py` | `/workshops/{wid}/separate` 入口 | `before-separate` | 确认 Tab2 请求传入的 wid 对应的 raw 状态。 |
| `src/kernel/kernel.py` | `raw_path is None` 抛错前 | `kernel-raw-missing` | 当 Kernel 内部判定 raw 缺失时，打印内存 state 与磁盘 state 的差异。 |

每条日志包含：

- `wid`
- `active_id`
- `kernel_id`
- `manager_id`
- `ws_id`
- `memory_raw`
- `disk_raw`
- `abs_raw`
- `abs_exists`
- `disk_error`

### 判断方式

如果 `after-upload-file.memory_raw` 有值，但 `before-separate.memory_raw` 为空：

- 说明同一个 wid 的运行时内存 state 在两次请求之间丢失或切换了对象。

如果 `kernel_id` / `manager_id` 在 upload 和 separate 之间不同：

- 说明请求命中了不同 Kernel/app 实例，需要检查 uvicorn reload、旧进程残留、端口上的服务是否唯一。

如果 `before-separate.memory_raw` 为空但 `disk_raw` 有值：

- 说明磁盘 state 是可信的，正式修复应在 `Kernel._load_workshop_raw_audio_into_rc()` 中加入 disk-state fallback。

如果 `before-separate.wid` 不是刚刚 upload 的 wid：

- 说明前端当前 workshop 状态或 Tab 切换逻辑有问题，需检查 `state.currentWid` 和 sidebar active 状态。

### 使用方法

1. 重启 UI 服务，确保修改生效：

```powershell
D:\anaconda\envs\pyq\python.exe -m src.ui
```

2. 在浏览器中重复：

```text
Tab1 上传音频 -> 进入 Tab2 -> 选择模型 -> 开始分离
```

3. 在终端中查找：

```text
[DEBUG-TAB2RAW]
```

4. 根据 `after-upload-*`、`before-separate`、`kernel-raw-missing` 三类日志判断根因。

### 清理要求

这是临时 debug instrumentation。确认根因并修复后，必须删除所有：

```text
[DEBUG-TAB2RAW]
```

相关代码。

---

## 十二、2026-07-13 追加：raw audio debug 结论与正式兜底修复

### 用户实测日志

用户按真实 UI 流程重启服务并复现后，终端输出：

```text
[DEBUG-TAB2RAW] before-separate {
  'wid': '614ef0804fe543e9',
  'active_id': '614ef0804fe543e9',
  'kernel_id': 3219369338736,
  'manager_id': 3219369344880,
  'ws_id': 3219369342480,
  'memory_raw': 'raw_audio\\梦的光点-王心凌.mp3',
  'disk_raw': 'raw_audio\\梦的光点-王心凌.mp3',
  'disk_error': None,
  'abs_raw': 'E:\\raungong\\tb\\TABsucks\\cache\\workshop_614ef0804fe543e9\\raw_audio\\梦的光点-王心凌.mp3',
  'abs_exists': True
}
```

### 结论

这条日志说明：

- Tab2 请求传入的 `wid` 是正确的；
- 当前 active workshop 与请求 wid 一致；
- API 层看到的 `memory_raw` 有值；
- 磁盘 `state.json` 中的 `disk_raw` 也有值；
- raw audio 绝对路径存在；
- `kernel_id` / `manager_id` / `ws_id` 在该请求内正常。

因此，最初那次 `Workshop <wid> has no raw audio` 更像是服务重启前的旧运行时内存状态，或浏览器/服务进程状态未同步造成的瞬时问题，而不是 Tab2 请求本身固定传错 wid。

### 正式修复

临时 debug instrumentation 已从源码中移除：

- 删除 `src/ui/api/analysis.py::_debug_raw_state()`
- 删除 `after-upload-file`
- 删除 `after-upload-url`
- 删除 `before-separate`
- 删除 `src/kernel/kernel.py` 中的 `[DEBUG-TAB2RAW] kernel-raw-missing` 打印

同时加入正式兜底：

```python
Kernel._recover_workshop_raw_audio_path(ws)
```

行为：

1. `Kernel._load_workshop_raw_audio_into_rc()` 先走原路径：`ws.get_raw_audio_path()`；
2. 如果内存 state 中 raw path 为空，则读取 `ws.cache.load_state()`；
3. 从磁盘 state 的 `TabState.Tab1.RawAudioFilePath` 恢复相对路径；
4. 使用 `ws.cache.to_absolute(rel_path)` 转为绝对路径；
5. 检查文件真实存在；
6. 将恢复出的 `rel_path` 同步回当前 `MusicWorkshop` 内存 state；
7. 继续调用 `load_audio()` 解码并写入 RC。

这样即使未来再出现“磁盘 state 有 raw path，但内存 state 丢了”的情况，Tab2 也可以自动恢复。

### API 错误处理

`POST /api/workshops/{wid}/separate` 现在会捕获启动阶段的同步异常：

```python
try:
    kernel.start_separation_task(...)
except Exception as e:
    _err(400, f"启动分离失败: {e}")
```

这避免 raw 缺失、路径非法、音频解码失败等启动前错误直接打穿成 uvicorn traceback。

### 验证

通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m py_compile `
  src\ui\api\analysis.py `
  src\kernel\kernel.py `
  tests\unit\test_kernel.py
```

手动验证 fallback：

```text
True
raw_audio\song.mp3
```

含义：

- 磁盘 state fallback 恢复出的路径与原 raw audio 文件一致；
- 内存 `Tab1.RawAudioFilePath` 被恢复为 `raw_audio\song.mp3`。

新增单测：

```python
TestKernel.test_recovers_raw_audio_path_from_disk_state
```

该测试只覆盖 state fallback，不触发 `load_audio()`、插件执行或分离任务，避免调试阶段的长链路卡顿。

---

## 十三、2026-07-13 追加：真实分离插件执行期间 UI 进度停在 0% 的修复

### 现象

用户在 UI 中点击 Tab2 分离后：

- 后端不再报 raw audio 缺失；
- 任务已经启动；
- UI 进度环一直显示 `0%`。

### 根因

`example_separator` 会主动调用 `progress_callback`，所以 mock 分离能持续推进进度。

但真实 `separation_bs_roformer` 插件当前是同步 `execute()`：

- 模型加载发生在 `execute()` 内部；
- 推理也发生在 `execute()` 内部；
- 插件本身没有在加载/推理过程中调用 `progress_callback`；
- 编排层原来只在执行前发 `0.0`，执行完成后发 `1.0`。

因此真实插件运行期间，前端只能看到启动时的 `0%`，要等整个模型跑完后才会跳到 `100%`。如果模型加载或推理较久，就表现为 UI 卡在 0%。

### 修复

在 `src/kernel/core/kernel_orchestrator.py::call_plugin_execute_async()` 的同步插件路径中加入 heartbeat progress：

1. 同步插件仍通过 `loop.run_in_executor(None, sync_run)` 放到线程池执行；
2. 如果调用方传入 `progress_callback`，编排层会包装一个 `emit_progress()`；
3. 插件自己调用进度时，仍优先使用插件进度；
4. 如果插件长时间不调用进度，编排层每隔 `progress_interval_sec` 发一个小幅递增进度；
5. heartbeat 上限为 `0.95`，避免任务未完成时显示 100%；
6. 外层 `Orchestrator.start_separation()` 在任务成功结束后仍显式 `cb(1.0)`。

默认心跳间隔从原先未使用的 `0.03s` 调整为 `0.5s`，避免真实长任务期间产生过多 SSE 事件。

### 影响范围

该修复只影响同步插件路径：

- 对真实 `separation_bs_roformer` 有效；
- 对未来不提供进度回调的同步插件也有效；
- 对提供 `run_async()` 的异步插件不改变行为；
- 对主动调用 `progress_callback` 的插件保留原进度，只做单调保护。

### 测试

新增测试：

```python
TestStartSeparation.test_sync_plugin_without_progress_gets_heartbeat
```

测试构造一个同步慢插件：

- `execute()` 内部只 `time.sleep(0.16)`；
- 不主动调用 `progress_callback`；
- 断言 `call_plugin_execute_async()` 会产生至少一个 `0 < progress < 1` 的中间进度。

验证命令：

```powershell
& D:\anaconda\envs\pyq\python.exe -m py_compile `
  src\kernel\core\kernel_orchestrator.py `
  tests\unit\test_kernel_orchestration.py
```

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_kernel_orchestration.py::TestStartSeparation::test_sync_plugin_without_progress_gets_heartbeat `
  -q
```

结果：

```text
1 passed
```

核心回归：

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_kernel_orchestration.py `
  tests/unit/test_resource_controller.py `
  tests/unit/test_plugin_manager.py `
  tests/unit/test_analysis_engine.py `
  -q
```

结果：

```text
49 passed
```

仍有环境 warning：

```text
PytestCacheWarning: could not create cache path ... .pytest_cache ... Access denied
```

该 warning 来自当前 Windows 权限环境，不影响测试通过。

---

## 十四、2026-07-13 追加：进度仍停在 0% 的二次定位与阶段进度修复

### 现象

第一次 heartbeat 修复后，用户反馈：

> 现在还是显示的 0%，怀疑根本没有开始用模型推理分离。

### 新判断

这说明问题可能发生在 `call_plugin_execute_async()` 之前，而不是同步 `execute()` 内部。

原 `Orchestrator.start_separation()` 的顺序是：

1. emit `separation_started`
2. `_ensure_plugin(resolved_plugin)`
3. `prepare_vram()`
4. `cb(0.0)`
5. `call_plugin_execute_async(...)`
6. `cb(1.0)`

如果卡在第 2 步或第 3 步，例如：

- import `src.plugins.separation.model_1.separator`
- import `audio_separator`
- 实例化真实插件
- 依赖报错
- VRAM 准备失败

则前端不会收到任何 `separation_progress`，只会看到点击按钮时 UI 自己显示的 `0%`。

另外，前端原来没有监听 `separation_failed`。如果后端已经发了失败事件，UI 仍会停在 0%，用户看不到失败原因。

### 修复

#### 1. 后端补阶段进度

在 `src/kernel/core/kernel_orchestrator.py` 中新增：

```python
emit_progress_event(bus, wid, event_type, progress, **extra)
```

并在真实分离链路中增加阶段进度：

| 阶段 | progress | stage |
|---|---:|---|
| 插件加载/实例化开始 | `0.01` | `loading_plugin` |
| VRAM 准备开始 | `0.03` | `preparing_vram` |
| 插件执行开始 | `0.05` | `running_plugin` |
| 同步插件执行中 | `0.05` -> `0.95` | heartbeat |
| 成功完成 | `1.0` | done |

这样即使真实插件还没进入模型推理，UI 也会看到“任务已经进入加载/准备阶段”，不再停在点击按钮时的 0%。

#### 2. 前端补失败事件

在 `src/ui/static/js/app.js` 中增加：

```javascript
.on('separation_failed', p => onSeparationFailed(p))
```

并新增：

```javascript
function onSeparationFailed(payload = {}) {
    const msg = payload.error || 'unknown error';
    showToast(`分离失败: ${msg}`, 'error');
    for (const suffix of ['', '-2']) {
        const label = document.getElementById(`sep-ring-label${suffix}`);
        if (label) label.textContent = 'failed';
    }
}
```

如果真实模型没有开始，是因为依赖、模型、VRAM、路径或运行时异常，用户现在会看到失败原因，而不是一直 0%。

### 验证

通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m py_compile `
  src\kernel\core\kernel_orchestrator.py
```

通过：

```powershell
node --check src\ui\static\js\app.js
```

通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_kernel_orchestration.py `
  -q
```

结果：

```text
9 passed
```

### 用户侧注意

由于修改了前端静态 JS，真实浏览器测试时需要：

1. 重启后端服务；
2. 浏览器强制刷新，避免使用旧 `app.js` 缓存。

建议：

```text
Ctrl + F5
```

或打开 DevTools 后勾选 Disable cache 再刷新。
---

## 十五、2026-07-13 追加：分离插件入口导入失败的定位与兼容修复

### 现象

用户在真实 UI 中点击 Tab2 开始分离后，前端弹出：

```text
分离失败：无法导入插件 'separation_bs_roformer' 的入口模块
'src.plugins.separation.model_1.separator': No module named
'src.plugins.separation.separator'
```

这说明任务已经进入后端插件加载阶段，但在导入 manifest 指向的入口模块
`src.plugins.separation.model_1.separator` 时，Python 先执行父包
`src.plugins.separation.__init__`，而旧父包初始化逻辑仍试图导入已经不存在的
`src.plugins.separation.separator`。

### 根因

分离插件迁移到 `src/plugins/separation/model_1/separator.py` 后，旧模块路径
`src.plugins.separation.separator` 已经不再是有效入口。只要父包初始化、类型兼容层或废弃
Workspace 代码仍引用旧路径，就可能在真实 UI 的插件加载链路里报：

```text
No module named 'src.plugins.separation.separator'
```

### 修复

1. `src/plugins/separation/__init__.py` 改为轻量 lazy exports：
   - 不再 eager import 旧 `src.plugins.separation.separator`；
   - 仅在访问 `SeparationPlugin`、`SeparationResult`、`SeparatorError`、`TrackId` 时，从
     `src.plugins.separation.model_1.separator` 延迟导入。
2. `src/kernel/core/analysis_engine.py` 的旧 fallback import 改为
   `src.plugins.separation.separator_old_type`。
3. 继续清理废弃兼容文件 `src/kernel/core/workspace.py` 中的旧路径：
   - `TYPE_CHECKING` 的 `SeparationResult` 改为从 `src.plugins.separation` 导入；
   - `get_analysis_target_data()` 的 `TrackId` 改为从 `src.plugins.separation` 导入。
4. 新增回归测试 `tests/unit/test_workspace_compat.py`，覆盖废弃 Workspace 读取
   `_separation_result` 时不会再触发旧 separation 模块路径。

### 验证

使用解释器：

```powershell
D:\anaconda\envs\pyq\python.exe
```

直接导入 manifest 入口通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -c "import importlib; m=importlib.import_module('src.plugins.separation.model_1.separator'); print(m.SeparationPlugin().name)"
```

结果：

```text
separation_bs_roformer
```

短回归通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_workspace_compat.py `
  tests/unit/test_plugin_manager.py::TestPluginManager::test_ensure_manifest_separator_imports_model_entrypoint `
  -q
```

结果：

```text
2 passed
```

核心回归通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_kernel_orchestration.py `
  tests/unit/test_resource_controller.py `
  tests/unit/test_plugin_manager.py `
  tests/unit/test_analysis_engine.py `
  tests/unit/test_workspace_compat.py `
  -q
```

结果：

```text
51 passed
```

仍存在当前 Windows 环境的 pytest cache 权限 warning：

```text
PytestCacheWarning: could not create cache path ... .pytest_cache ... Access denied
```

该 warning 不影响上述业务回归结果。

### 用户侧验证注意

由于截图中的报错来自正在运行的 UI 服务，修复后需要重启服务进程，并让浏览器重新加载静态资源：

```powershell
D:\anaconda\envs\pyq\python.exe -m src.ui
```

浏览器建议 `Ctrl + F5` 强制刷新，避免继续使用旧 `app.js` 或旧后端进程状态。

---

## 十六、2026-07-13 追加：Tab2 raw audio 改为多声道加载

### 背景

用户指出当前 `Kernel._load_workshop_raw_audio_into_rc()` 使用：

```python
load_audio(raw_path, sr=sample_rate)
```

这会把原始音频混成单声道，并按传入 `sample_rate` 重采样。对 Tab2 音轨分离来说，真实分离插件更适合拿到原始多声道输入，避免在进入模型前丢失左右声道信息。

### 链路检查

沿 Tab2 到分离结束的链路检查结果：

1. `load_audio_multi_channel()` 返回 `AudioData.samples` 形状为 `(channels, samples)`，并保留源文件原采样率。
2. `separation_bs_roformer` 插件在 `execute()` 中已经按 `(channels, samples)` 处理 raw buffer；如果是一维才补成二维。
3. 真插件 `_separate()` 写临时 wav 时使用 `audio.T`，正好转成 soundfile 需要的 `(samples, channels)`。
4. `example_separator` 对二维 raw 使用 `raw.shape[1]` 作为样本数，不会因多声道输入崩。
5. `Kernel._persist_separated_tracks()` 保存 stem 前已有 `_normalize_audio_samples_for_save()`，会把明显的 `(samples, channels)` 转成项目约定的 `(channels, samples)`。

因此在 Tab2 分离到写回 `TrackAudioFilePath` 的范围内，没有发现会被该替换直接打断的 shape bug。

### 修改

`src/kernel/kernel.py` 中：

```python
from src.audio.loader import load_audio_multi_channel

audio = load_audio_multi_channel(raw_path)
```

替换原先：

```python
from src.audio.loader import load_audio

audio = load_audio(raw_path, sr=sample_rate)
```

行为变化：

- 不再强制转单声道；
- 不再强制重采样到默认 `22050`；
- `ResourceController["raw"]` 保存原始声道布局；
- `ResourceController.metadata["sample_rate"]` 保存源文件实际采样率。

### 回归测试

新增测试：

```python
TestKernel.test_load_workshop_raw_audio_uses_multi_channel_loader
```

该测试 monkeypatch `load_audio_multi_channel()`，确认：

- Kernel 调用的是 multi-channel loader；
- RC 中的 raw buffer 保持 `(2, 128)`；
- metadata 中的 sample_rate 使用 loader 返回的 `48000`，而不是调用参数中的 `22050`。

### 验证命令

```powershell
& D:\anaconda\envs\pyq\python.exe -m py_compile `
  src\kernel\kernel.py `
  tests\unit\test_kernel.py
```

```powershell
& D:\anaconda\envs\pyq\python.exe -m pytest `
  tests/unit/test_kernel.py::TestKernel::test_load_workshop_raw_audio_uses_multi_channel_loader `
  tests/unit/test_kernel.py::TestKernel::test_start_separation_task_persists_tab2_tracks `
  -q
```

实际验证结果：

- `py_compile` 通过；
- pytest 在当前 Windows 环境中仍被临时目录权限拦截，未进入业务断言：

```text
PermissionError: [WinError 5] 拒绝访问。:
'C:\\Users\\goneday\\AppData\\Local\\Temp\\pytest-of-goneday'
```

以及使用仓库内 `--basetemp=.pytest_run_tmp_multi_audio` 时，pytest cleanup 阶段仍出现同类权限错误。该问题与此前 `.pytest_cache` / `.pytest_tmp` 权限 warning 属同一类环境问题。

补充手动验证通过：

```powershell
& D:\anaconda\envs\pyq\python.exe -c "<manual Kernel + monkeypatch load_audio_multi_channel check>"
```

输出：

```text
multi-channel raw load ok (2, 128) 48000
```

含义：

- `Kernel._load_workshop_raw_audio_into_rc()` 已走 `load_audio_multi_channel()`；
- RC 中 `raw` buffer 保持 `(channels, samples)`；
- `sample_rate` 使用 loader 返回的真实采样率 `48000`。
