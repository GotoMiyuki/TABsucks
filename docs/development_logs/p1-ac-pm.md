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
