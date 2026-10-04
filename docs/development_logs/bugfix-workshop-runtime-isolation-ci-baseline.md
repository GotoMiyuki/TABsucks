# TABsucks 开发日志 · Bugfix-01 / vUnreleased

*日志日期：2026-09-16 | 编写人：Codex*

---

## 1. 上下文快照

- **分支 / Tag**：当前工作分支 `main`；变更分类 `bugfix/workshop-runtime-isolation-ci-baseline`；尚未创建发布 Tag
- **依赖变动**：否。未修改运行时或开发依赖版本；CI 仅安装仓库已有依赖及既有版本区间内的测试工具
- **关联文档/Issue**：本次代码仓库架构审查结论；[开发指导](../development_guide.md)；正式 Bug/PR 编号待补充

---

## 2. 本期摘要

本次变更定义为 bugfix，集中修复测试基线失效、跨工作坊共享运行时状态以及 GPU 插件可并发超配三个问题。Orchestrator 由单一全局 ResourceController 改为每个工作坊独立持有 ResourceController、PluginManager 和 AnalysisEngine，并通过工作坊锁隔离同一工作坊内的任务执行。分离与分析阶段的 GPU 插件统一经过进程级信号量，避免同时申请显存。测试收集错误、过期测试假设和失效打包引用一并清理，并新增 Python、JavaScript、Ruff 三类 CI 门禁；最终非慢速、非联网 Python 测试与全部 JavaScript 测试通过。

---

## 3. 核心内容详解

### 3.1 架构与设计决策

- **数据结构选型**：以 `ExecutionContext` 数据类聚合 RC、PM、AE，并由 `dict[workshop_id, ExecutionContext]` 管理。相比继续在全局 RC 中拼接工作坊前缀，这种边界能同时隔离音频缓冲区、元数据、插件实例和模型缓存，减少遗漏命名空间的风险。
- **并发控制选型**：每个工作坊使用独立的异步锁，确保同一工作坊的分离与分析任务不会同时读写上下文；不同工作坊的 CPU 任务仍可并行。所有 Orchestrator 共享一个进程级 GPU 信号量，GPU 插件在完成显存准备、执行和释放后才交出许可。
- **兼容策略**：保留 `orchestrator.rc`、`orchestrator.pm`、`orchestrator.ae` 作为默认上下文别名，使已有直接使用 Orchestrator 的调用方和测试可以渐进迁移。首个工作坊绑定默认上下文，后续工作坊创建独立上下文。
- **生命周期边界**：工作坊关闭或删除时释放对应上下文，Kernel 关闭时释放全部上下文。持久化后的音频轨道仍归 WorkshopCache 管理，ExecutionContext 只负责进程内运行时资源。
- **质量门禁策略**：仓库现有全量 Ruff 配置会触发约 845 条历史样式问题。为避免新增 CI 从第一天起永久失败，本期先对全仓启用语法、未定义名称等正确性规则，同时要求本次修改文件通过完整 Ruff 规则；历史样式债后续分批清理。
- **测试资产取舍**：删除仅引用已移除实现的 `test_separator.py` 和 `test_chordnet_2e1d.py`，保留并加强当前正式插件、Kernel 和 Orchestrator 的测试。`packaging/tabsucks.spec` 中对应的失效 hidden import 同步删除。

**流程 / 状态机**：

```text
Kernel 收到工作坊任务
  -> 按 workshop_id 获取或创建 ExecutionContext
  -> 获取该工作坊任务锁
  -> CPU 插件：直接执行
  -> GPU 插件：等待全局 GPU 许可 -> 准备显存 -> 执行 -> 释放显存
  -> 持久化结果到该工作坊缓存
  -> 关闭/删除工作坊时清理对应上下文
```

**接口 / 模块边界约定**：

- `Orchestrator.get_context(workshop_id)` 是获取工作坊运行时资源的统一入口。
- `Kernel` 负责将持久化音频加载到正确上下文，并将插件结果写回 WorkshopCache。
- 插件仍只面向传入的 ResourceController 工作，不需要感知工作坊 ID。
- CI 负责持续验证测试收集、核心正确性规则和前后端单元测试，不负责解决全部历史格式债。

### 3.2 关键代码实现详解

**改动点 A：工作坊级 ExecutionContext**

- **意图**：消除所有工作坊共享同一个 RC/PM/AE 时，同名 `raw`、`vocals` 等缓冲区互相覆盖或误复用的问题。
- **关键代码**：

  ```python
  # ❌ 之前：所有工作坊共享同一组运行时对象
  self.rc = ResourceController()
  self.pm = PluginManager(self.rc)
  self.ae = AnalysisEngine(self.rc, self.pm)

  # ✅ 现在：按 workshop_id 获取独立上下文
  @dataclass
  class ExecutionContext:
      rc: ResourceController
      pm: PluginManager
      ae: AnalysisEngine

  context = orchestrator.get_context(workshop_id)
  context.rc.set_buffer("raw", samples)
  ```

**改动点 B：工作坊锁与全局 GPU 信号量**

- **意图**：同一工作坊内避免任务读写竞争，同时阻止不同工作坊的重型 GPU 插件同时占用显存。
- **关键代码**：

  ```python
  # ✅ 工作坊级串行边界
  async with workshop_lock:
      if is_gpu_plugin:
          # ✅ 进程级 GPU 串行边界
          async with self._gpu_semaphore:
              vram = context.pm.prepare_vram(plugin_name)
              result = await execute_plugin()
              context.rc.release_vram(plugin_name)
      else:
          result = await execute_plugin()
  ```

**改动点 C：可落地的 CI 基线**

- **意图**：在保留历史样式债信息的同时，先建立能够稳定通过并阻止正确性回退的自动化门禁。
- **关键代码**：

  ```yaml
  # ✅ 全仓正确性检查
  - run: ruff check --select E9,F63,F7,F82 src tests

  # ✅ Python 回归测试，排除显式标记的慢速/联网测试
  - run: pytest -m "not slow and not network"

  # ✅ 无额外 npm 依赖的前端单元测试
  - run: node --test tests/js/*.test.mjs
  ```

**附带缺陷修复**：

- 修正旧 `WorkspaceManager.close()` 将 `_active_id` 误写成 `_active_ids`，导致关闭当前工作区后无法切换到剩余工作区的问题。
- 更新 Bass Root 插件测试，使断言匹配标准 `{status, data}` 返回结构，并补齐无外部节拍时的 librosa mock。
- HTTP API 测试不再依赖已经移除的“自动生成假音频”行为，而是显式模拟任务入口或准备真实缓存文件。
- ChordMini 子模块未初始化时，相关模型执行测试明确标记为 skip；CI 使用递归子模块检出后执行该路径。

---

## 4. 调试踩坑时间线

| 轮次 | 我的操作 / 触发条件 (Action) | 系统报错 / 观察结果 (Observation) | 最终修正决策 (Decision) |
| :--- | :--------------------------- | :-------------------------------- | :---------------------- |
| 1 | 执行全仓 pytest 收集 | 三个测试分别引用不存在的 `src.core.workspace`、`src.separation.separator` 和已删除的 `chordnet_2e1d` | 修正仍有效的兼容模块导入；删除只覆盖已淘汰实现的测试；同步清理 PyInstaller hidden import |
| 2 | 首次将 `ruff check src tests` 作为 CI 命令 | 暴露约 845 条历史样式与命名问题，新 CI 无法形成绿色基线 | 全仓先启用正确性规则，本次修改文件执行完整规则；把全量样式治理列为后续任务 |
| 3 | 使用本机临时 Python 环境运行 pytest | pytest 默认临时目录出现 `WinError 5` 权限错误 | 使用明确的 `--basetemp` 安全临时目录完成回归，不修改仓库测试逻辑绕过问题 |
| 4 | 修复收集错误后运行完整非慢速测试 | 出现 5 个失败和 2 个错误，主要来自旧返回结构、过期 HTTP mock 假设及未初始化 ChordMini 子模块 | 更新测试以匹配当前 API；子模块缺失时明确 skip；CI 配置 `submodules: recursive` |
| 5 | 验证跨工作坊并发分离 | 原结构中两个任务会共享 RC 和插件实例，显存分配只记录预算、不阻止并发 | 引入工作坊 ExecutionContext、工作坊锁及进程级 GPU 信号量，并添加最大并发数为 1 的回归测试 |

**最终验证结果**：

- Python：收集 552 项；排除 5 项 slow/network；545 passed、2 skipped。
- JavaScript：3 passed。
- Ruff：全仓正确性规则通过；本次修改文件完整规则通过。
- 其他：`compileall`、CI YAML 解析、`git diff --check` 通过。

---

## 5. 下一步计划

- **[ ] 明确待办（Todo，由人类开发者确定后填写）**：
  1. 分批清理历史 Ruff 样式债，最终将 CI 从正确性规则提升为完整 `ruff check src tests`。
  2. 在本地初始化 ChordMini 子模块并补跑当前跳过的 2 项模型执行测试，同时确认 GitHub Actions 递归检出路径稳定。
  3. 继续处理架构审查中的下一优先级：建立 Kernel 任务注册、取消、关闭等待和终态收敛机制。
  4. 为工作坊上下文增加可观测指标，例如活动任务数、缓冲区占用、模型缓存与 GPU 等待时间。
- **❗️ 阻塞项 / 待确认疑点（Blockers）**：
  - 正式 Bug 编号、PR 编号和目标发布版本尚未分配；本日志暂记为 `vUnreleased`。
  - 当前桌面运行时按单一 asyncio 事件循环设计全局 GPU 信号量；若未来允许同一进程内多个独立事件循环同时调度 GPU，需要改为跨事件循环的进程级调度器。

---