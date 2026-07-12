# Plugin 编排说明（Workshop ↔ Kernel ↔ Plugin Manager ↔ Analysis Engine）

> 来源：本分支 `p1-workshop-cache-system` 的核心设计。
> 状态：MVP 跑通的最小实现已落地（175 unit tests pass），文档随代码演进。
>
> 关联模块：
>
> * [src/kernel/core/kernel.py](../../src/kernel/kernel.py) — 顶层 `Kernel` 进程入口
> * [src/kernel/core/kernel_orchestrator.py](../../src/kernel/core/kernel_orchestrator.py) — `Orchestrator` 编排层
> * [src/plugins/_example_separator](../../src/plugins/_example_separator/) — MVP 范例
> * [src/plugins/_example_analyzer](../../src/plugins/_example_analyzer/) — MVP 范例
> * 会议 [docs/meetings/2026-6-16-meeting.md](../../meetings/2026-6-16-meeting.md) §2 — PM/AE/RC 职权划分

---

## 一、目标

让 MVP 阶段**真实跑通**从 UI 点击到 plugin 执行的完整链路，并在此基础上：

1. **Plugin 写作者有清晰模板**（_example_*）
2. **其他同事**（PM/RC/AE）知道接口契约在哪
3. **未来接入真 plugin**（BS-RoFormer / madmom / ismir2019）的替换点明确

## 二、架构图

```
┌──────────────────────────────────────────────────────────────┐
│   Browser  (HTML + JS in src/ui/static/)                     │
│   触发：                                                       │
│   - GET  /api/plugins/separators                             │
│   - POST /api/workshops/{wid}/separate                       │
└────────┬──────────────────────────────────────┬──────────────┘
         │ FastAPI BackgroundTasks              │ SSE /api/events
         ▼                                      ▲
┌──────────────────────────────────────────────────────────────┐
│   src/ui/api/                                                  │
│   - analysis.py  → Kernel.start_separation_task              │
│   - events.py    → Kernel.bus.subscribe (SSE 转发)            │
│   - plugins.py   → Kernel.list_separator_plugins             │
└────────┬──────────────────────────────────────┬──────────────┘
         │                                      │
         ▼                                      │ events
┌──────────────────────────────────────────────────────────────┐
│   src/kernel/kernel.py::Kernel                                │
│   - event_bus: EventBus                                       │
│   - manager:    WorkshopManager                              │
│   - orchestrator: Orchestrator  ← 新                         │
└────────┬─────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│   src/kernel/core/kernel_orchestrator.py::Orchestrator       │
│                                                                  │
│   ┌─────────────┐    ┌─────────────────┐                       │
│   │ Resource-   │◄──►│ PluginManager   │   (RC)                  │
│   │ Controller  │    │  - register     │   (PM)                  │
│   │             │    │  - execute      │                        │
│   └─────────────┘    └────────┬────────┘                       │
│                               │                              │
│                               ▼                              │
│                  ┌────────────────────────┐                │
│                  │   AnalysisEngine       │  (AE)            │
│                  │   run(progress_cb)     │                  │
│                  └────────────────────────┘                │
│                              │                              │
│   start_separation() ──────►│                              │
│   start_analysis()   ──────►│                              │
│                              ▼                              │
│                    调 pm.execute(plugin_name, rc=rc,       │
│                                    **kwargs)               │
│                              │                              │
│                              ▼                              │
│   ┌─────────────────────────────────────────────┐         │
│   │  Plugin.execute(rc, **kwargs) → dict       │  (plugin) │
│   │  - 读 rc.get_buffer("raw") / get_metadata()│         │
│   │  - 异步跑实际推理（mock：asyncio.sleep）  │         │
│   │  - 周期性回调 progress_callback(progress) │         │
│   │  - 写 rc.set_buffer("vocals"/"drums"/...) │         │
│   │  - 返回 {status, data: {stems:[...]}}     │         │
│   └─────────────────────────────────────────────┘         │
│                              │                              │
│                              ▼                              │
│   bus.emit("wid", "separation_done", {stems})               │
└──────────────────────────────────────────────────────────────┘
```

## 三、时序：分离任务端到端

```
[Browser] POST /api/workshops/abc/separate
     │
     ▼
[analysis.py trigger_separation] ─► BackgroundTasks.add_task
     │
     ▼ async
[Kernel.start_separation_task(wid, plugin_name)]
     │
     ▼
[Orchestrator.start_separation(wid, bus, plugin_name)]
     │
     ├── bus.emit("abc", "separation_started", {plugin})
     │
     ▼   await call_plugin_execute_async(plugin, rc)
     │
[plugin.execute(rc, progress_callback=cb)]
     │
     ├── for step in 0..100:
     │     cb(step/100)
     │   (cb 内 → bus.emit("abc", "separation_progress", {progress, step}))
     │
     ├── rc.set_buffer("vocals", arr)
     ├── rc.set_buffer("drums", arr)
     ├── ... (6 道)
     ├── rc.set_metadata("separated_stems", [...])
     │
     └── returns {status: "success", data: {stems: [...]}}
     │
     ▼
[Orchestrator._run] bus.emit("abc", "separation_done", {stems})
     │
     ▼
[SSE 推到 Browser]              [HTTP 200 {ok, task}]
     │
     ▼
[app.js EventSource.onmessage]
     ├── separation_progress → 更新进度环
     └── separation_done    → 切到 Tab3 / 6 道 wav 列表
```

## 四、Plugin 接口（不可改）

源自 [src/plugins/__init__.py](../../src/plugins/__init__.py) 与同事 `p1-ac-pm` 分支的既成契约：

```python
from abc import ABC, abstractmethod
from typing import Any

class Plugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...        # str ID, e.g. "separation_bs_roformer"

    @property
    @abstractmethod
    def version(self) -> str: ...     # "0.0.1"

    @abstractmethod
    def execute(self, rc, **kwargs) -> dict[str, Any]:
        """执行；返回值必须含 'status'。"""
        ...
```

**关键约束**：
1. plugin 通过 `rc.get_buffer / set_buffer / get_metadata / set_metadata` 与 ResourceController 通信
2. plugin 通过 `progress_callback`（0~1）上报进度（**禁止**自己 emit bus）
3. plugin 不直接做文件 IO（写盘由调用方 Orchestrator/MusicWorkshop 负责）

## 五、Orchestrator 接口（新增）

```python
class Orchestrator:
    def __init__(self, *, rc=None, pm=None, register_examples=True): ...

    def list_separator_plugins() -> list[dict]   # 给 UI 下拉列表
    def list_analyzer_plugins()  -> list[dict]

    def start_separation(wid, bus, *, plugin_name, audio_samples=None,
                         sample_rate=22050, durations_sec=3.0) -> asyncio.Task
    def start_analysis(wid, bus, *, plugin_name, stem_name="vocals",
                       durations_sec=1.5) -> asyncio.Task
```

返回的 `asyncio.Task` 在 `asyncio.run()` / FastAPI BackgroundTasks 异步上下文中跑。**同步上下文** 调用 `start_separation` 会创建新 event loop（MVP 兜底，**P1 应改为异步调用**）。

## 六、Kernel 暴露方法（供 HTTP 层用）

```python
class Kernel:
    def list_separator_plugins() -> list[dict]   # 转 orchestrator
    def list_analyzer_plugins()  -> list[dict]

    def start_separation_task(wid, *, plugin_name="example_separator",
                              audio_samples=None, sample_rate=22050,
                              durations_sec=3.0) -> asyncio.Task
    def start_analysis_task(wid, *, plugin_name="example_analyzer",
                            stem_name="vocals", durations_sec=1.5) -> asyncio.Task
```

UI 层调 `kernel.start_separation_task(wid)`，不需要直接 import orchestrator。

## 七、HTTP 端点（新增 / 修改）

| Method | Path | 实现 |
|--------|------|------|
| GET | `/api/plugins/separators` | 真实现：`Kernel.list_separator_plugins()` |
| GET | `/api/plugins/analyzers` | 真实现：`Kernel.list_analyzer_plugins()` |
| POST | `/api/workshops/{wid}/separate` | **真实现**：调 `Kernel.start_separation_task`，通过 BackgroundTasks fire-and-forget |
| POST | `/api/workshops/{wid}/analyze` | **真实现**：调 `Kernel.start_analysis_task` |
| GET | `/api/events` | SSE — 见 [HTTP_API.md](../../HTTP_API.md) |

SSE event payload 字段（与会议 §5.2 一致）：
```json
{
  "type": "separation_progress",
  "payload": {"progress": 0.5},
  "workshop_id": "abc",
  "emitted_at": 1783765628.0
}
```

## 八、上下游协作（团队对接）

### 8.1 其他同事如何扩展 PM

未来走 manifest 扫盘（`p1-ac-pm` 已实现 `SeparationPluginManager`），**新增目录**：

```
src/plugins/separation/model_mynew/
    __init__.py           # SeparationPlugin + manifest
    manifest.json         # {name, class, entrypoint, requirements, phase}
    model weights / code  # 见 audio-separator 文档
```

`Kernel.list_separator_plugins()` 改为调 `pm.get_available_plugins()` 即可。

### 8.2 其他同事如何扩展 AE

`AnalysisEngine.run(progress_callback)` 已经能调度整个流水线。未来拆出 stage 子方法（`_run_rhythm / _run_separation / _run_chord`），Orchestrator 现在调的是 `engine.run()` 的等价 fragment（mock 通过 plugin.execute）。

### 8.3 RC 集成

`p1-ac-pm` 分支的 `ResourceController_s`（thread-safe + VRAM 配额）通过修改 Orchestrator 的 `__init__`：
```python
from src.kernel.core.resource_controller_s import ResourceController_s
orch = Orchestrator(rc=ResourceController_s())
```

MVP 阶段使用基类 `ResourceController`。**P1 切换**到 `_s` 版本后自动获得：
* `threading.RLock` 保护
* `allocate_vram` / `release_vram`（在大型 BS-RoFormer 跑前预防 OOM）

## 九、已跑通的端到端链路（测试覆盖）

`tests/unit/test_kernel_orchestration.py` 8 个测试：

| 测试 | 验证点 |
|------|--------|
| `test_orchestrator_registers_default_plugins` | PM 自动注册 example_separator / example_analyzer |
| `test_orchestrator_lists_plugin_metadata` | 下拉列表 API 返回 metadata |
| `test_callback_emits_to_bus` | progress_callback 推到 EventBus |
| `test_callback_includes_extra` | extra={track: vocals} 被合并到 payload |
| `test_emits_started_progress_done` | 完整链路 started → progress×N → done |
| `test_emits_done_with_stems` | done 事件 payload 含 6 道 stem 名 |
| `test_unknown_plugin_emits_failed` | 不存在的 plugin → failed 事件 |
| `test_emits_done_with_chords` | analysis 完成带 4 个 chord |

跑 `pytest tests/unit/test_kernel_orchestration.py -v` 0.4 秒内全部通过。

## 十、迭代路径

| 阶段 | 任务 | 估时 |
|------|------|------|
| **MVP (已完成)** | 上面 5 个 Phase 全部完成 | 1.5 天 |
| **P1-A** | 把 `_example_*` 替换为真 BS-RoFormer plugin（基于 `p1-ac-pm` 的 `model_1`） | 0.5 天 |
| **P1-B** | 把 Orchestrator 的 `_run` 改为调 `AnalysisEngine.run()` 全流水线 | 0.5 天 |
| **P1-C** | 切换 `ResourceController_s` 并接 VRAM 配额 | 0.5 天 |
| **P1-D** | 错误处理：plugin 失败 → kernel.bus emit `*_failed`（已部分做）+ UI 弹错 + reload 上次结果 | 1 天 |
| **P1-E** | 多进程：plugin 跑在 `multiprocessing.Pool` 里，避免主进程阻塞 | 1 天 |

## 十一、已知坑 & 注意事项

1. **同步上下文创建 task**：`Orchestrator.start_separation()` 在测试或同步模块里调用时会**新开 event loop**。正常情况下应**仅在 async 上下文**调用（FastAPI BackgroundTasks / pytest-asyncio）。如果是 sync 调用，跑完后 task 会被警告 "Task was destroyed but it is pending"。
2. **plugin.run_async 优先**：`call_plugin_execute_async` 优先调 plugin 自己的 `run_async` 协程。如果只有 `execute`（同步），走 `loop.run_in_executor`。
3. **测试 timeout**：`_drain_queue_until_terminal` 等到 `separation_done / failed` 之一，最多 2 秒。若测试 flaky，调高 `extra_timeout`。
4. **EventBus 与 QT / GUI 集成**：MVP 阶段 EventBus 是进程内 Queue。未来 Qt 主线程集成时，把 `bus.emit` 切换到 Qt signal/slot。

## 十二、快速自测

```bash
# 单元测试
pytest tests/unit/test_example_plugins.py tests/unit/test_kernel_orchestration.py -v

# 启动服务 + 用 curl 测端到端
python -m src.ui
# 浏览器：localhost:8000 → 新建车间 → Tab1 上传 → 切 Tab2 → 选 example_separator → NEXT
# 观察 sep 进度环丝滑 → 完成后 6 道 wav 列表

# 直接通过 SSE 观察事件
curl -N http://localhost:8000/api/events
```

---

## 十三、待办（与 [p1-workshop-cache-system.md](p1-workshop-cache-system.md) 不重叠的新增条目）

- [ ] **TODO-PLUGIN-A1**：把 `p1-ac-pm` 的 manifest 扫盘接入 `Kernel.list_*_plugins()`
- [ ] **TODO-PLUGIN-A2**：切换 `ResourceController_s`（一旦 PM 分支 merge 进来）
- [ ] **TODO-PLUGIN-A3**：错误处理——plugin 抛异常时 UI 弹模态框而非仅 send `*_failed`
- [ ] **TODO-PLUGIN-A4**：URL 上传 path 还没接（Tab1 仅本地上传生效；URL 待 `audio/loader.py::load_audio_from_url` 集成）
- [ ] **TODO-PLUGIN-A5**：stale 检测（输入文件改了 → 旧分析 stale，提示重跑）
- [ ] **TODO-PLUGIN-A6**：MIDI 导出接 Kernel（`MidiExporter` stub 已有）
- [ ] **TODO-PLUGIN-A7**：Tab4 6 轨混合 wav 端点（`get_mix_audio()`）
- [ ] **TODO-PLUGIN-A8**：AudioPlayer 接 Workshop（播放 / 暂停 / seek / 调速 / A-B loop）
- [ ] **TODO-PLUGIN-A9**：CSS polish（welcome-panel / btn-danger / busy-overlay）

> 上面 9 个等团队协调推进。
