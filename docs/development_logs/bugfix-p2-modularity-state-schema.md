# TABsucks 开发日志 · Bugfix-03 / vUnreleased

*日志日期：2026-09-26 | 编写人：Codex*

---

## 1. 上下文快照

- **分支 / Tag**：`bugfix/workshop-runtime-isolation-ci-baseline`；未新建 Tag，本期尚未 commit / push。
- **依赖变动**：否。使用用户配置的项目 `.venv`（Python 3.12.14）与本机 Node.js 24.19.0；没有安装、升级依赖，也未改动用户已有的 `constraints-windows.txt` 修改。
- **关联文档/Issue**：[开发指导](../development_guide.md)、[当前架构](../架构设计.md)、[实现状态](../implementation_status.md)、[HTTP API](../HTTP_API.md)、[上一期 P1 日志](bugfix-p1-task-audio-contract.md)。本期没有关联正式 Issue 编号。
- **工作区边界**：保留本期开始前的 P1、文档及用户环境配置修改；不覆盖已有日志。本期的 P2 指架构审查问题，不包含 `plan.md` 中插件市场/SDK 等产品路线图。

## 2. 本期摘要

先在用户配置好的环境运行基线：Python 555 项通过、2 项跳过、5 项未选择，JavaScript 3 项通过。随后拆分 Workshop、Kernel 任务执行、HTTP 工作流 API、前端控制器和样式文件，并统一事件总线及兼容入口，移除未引用的旧实现和伪 MIDI 写出路径。`state.json` 新增 v1 schema、纯迁移函数、升级前备份、原子提交和未来版本拒写保护。最终 Python 576 项、JavaScript 4 项通过，Ruff correctness、改动核心文件 F/I 检查、pip check 和 diff check 通过；本轮不等同于真实模型或浏览器视听端到端验收。

## 3. 核心内容详解

### 3.1 架构与设计决策

- **数据结构选型**：继续使用 Workshop dataclass，不引入数据库；以根字段 `SchemaVersion` 显式标识持久化契约。迁移函数深拷贝输入，避免在校验失败前改变调用方数据。新版本迁移必须逐版本增加，不能只修改读取默认值。
- **流程/状态机**：读取 → 版本与字段校验 → running 恢复为 interrupted → 备份原字节 → 原子提交 → 注册运行时。失败跳过当前车间并保留原状态；未来版本一律不降级重写。
- **接口/模块边界约定**：Kernel 负责生命周期，组合服务 WorkshopJobs 负责执行与提交，Orchestrator 负责插件调度，TaskService 继续持有任务句柄。Workshop 的状态、运行时和管理器分文件；旧 `workshop.py` 只重导出。HTTP 拆为 analysis/uploads/media/dependencies。前端以共享状态和显式回调装配分离、分析、播放控制器，不互相循环导入；CSS 保持原级联顺序。
- **兼容与删除策略**：只保留必要的薄入口：Kernel 事件类型重导出、旧 Workshop 导入、旧 Workspace 隔离实现、`_s` 类别名。移除旧 Separator、两份 `.deprecated` 插件副本、未使用的 Mixer/Timeline；这些代码可从 Git 恢复，没有删除用户缓存或音频。音频分离结果并非 MIDI 转录，旧占位 API 明确报错且不创建文件；真实和弦 MIDI 导出保留。
- **无数据语义**：可视化不再生成随机波形或默认假节拍；缺失音频/分析时返回空数组及元数据标识。节奏结果的 BPM 仍可推算节拍位置，不宣称逐拍检测精度。

### 3.2 关键代码实现详解

**改动点 A：版本边界**

- **意图**：阻止字符串、bool 版本号或未来版本被旧代码误读后覆盖。
- **关键代码**（`state_migrations.py`）：

  ```python
  version = raw.get("SchemaVersion", 0)
  if type(version) is not int or version < 0:
      raise ValueError("SchemaVersion 必须是非负整数")
  if version > CURRENT_SCHEMA_VERSION:
      raise ValueError(f"Unsupported future SchemaVersion: {version}")
  result = deepcopy(raw)
  ```

**改动点 B：备份与原子迁移**

- **意图**：首次升级前保留原字节；既不覆盖已有备份，也不在备份冲突时继续写状态。
- **关键代码**（`workshop_manager.py`）：

  ```python
  if not backup.exists():
      with backup.open("xb") as target:
          target.write(original)
  if backup.read_bytes() != original:
      raise ValidationError("迁移备份与原文件不一致，保留 state.json 等待人工确认")
  # 随后仍通过 WorkshopCache 的临时文件 + 原子替换提交。
  cache.save_state(state.to_dict())
  ```

**改动点 C：真实前端模块测试**

- **意图**：不再从巨型 app.js 删除 import 后在 VM 中拼接测试；测试实际模块边界，捕获拆分遗漏的依赖。
- **关键代码**（`tests/js/tab3_run_all.test.mjs`）：

  ```javascript
  import { createAnalysisController } from '../../src/ui/static/js/analysis_controller.js?v=20260926p2';
  const app = { state, ...createAnalysisController({
      showToast() {}, updateNavigationControls() {},
  }) };
  ```

### 3.3 验证结果

| 检查 | 结果 |
|---|---|
| 基线 pytest，排除 slow/network | 555 passed、2 skipped、5 deselected |
| 最终相同范围 pytest | 576 passed、2 skipped、5 deselected；新增 21 项 P2 专项用例 |
| Node 测试 | 4 passed（真实模块图初始化、批量任务串行调度、两项时间轴缩放） |
| Ruff correctness：`E9,F63,F7,F82`，src/tests | 通过 |
| 拆分后核心模块及专项测试 Ruff F/I | 通过 |
| Black | 本期拆分 Python 文件已格式化 |
| pip check | No broken requirements found |
| git diff --check | 通过 |
| CSS 内容与级联顺序核对 | 拼接四份样式后与拆分前一致（忽略空白） |

最终命令（仓库根目录执行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -m 'not slow and not network' --basetemp .venv/test-p2-complete -q
node --test tests/js/*.test.mjs
.\.venv\Scripts\python.exe -m ruff check --select E9,F63,F7,F82 src tests
.\.venv\Scripts\python.exe -m pip check
```

专项覆盖迁移纯函数与幂等性、非法/未来版本拒写、嵌套损坏字段、备份冲突、原子提交失败重试、当前版本不重写、坏车间隔离、重启中断状态、兼容别名同一性、旧 MIDI 不覆盖文件和缺失分离插件明确失败。两项 BTC 模型用例跳过；五项 slow/network 用例未执行。依赖层仍出现 FastAPI/Starlette、audioread 的弃用警告，本期未通过升级依赖处理。

## 4. 调试踩坑时间线

| 轮次 | 我的操作 / 触发条件（Action） | 系统报错 / 观察结果（Observation） | 最终修正决策（Decision） |
|---|---|---|---|
| 1 | 拆分 Kernel 的 worker helper 后执行回归 | P1 测试仍从旧模块导入私有 helper，收集失败 | 共享 worker helper 收敛到 async_workers，保留旧入口重导出 |
| 2 | 新增 schema 字段并拆分前端 | 状态根键集合断言和 app.js 字符串位置断言失败 | 更新 schema 断言；静态检查改指向职责模块，新增实际模块图初始化测试 |
| 3 | 首次编写迁移专项测试 | 测试车间 ID 使用 old/good，不符合缓存 ID 只允许 hex/- 的规则 | 修正测试 fixture 为合法 ID，不放宽生产约束 |
| 4 | 检查损坏 Tab4 的恢复行为 | volume 为 null 会引发 TypeError，原加载边界没有统一处理 | 在 WorkshopState 反序列化处转为 ValidationError，坏车间不影响其他车间 |
| 5 | 检查备份失败和重试 | 部分或旧备份不能被假定为本次迁移原文 | 校验已有备份与原文件字节一致，否则保留状态并明确报告冲突 |
| 6 | 同步架构文档 | 旧文档说无 CodeQL，但仓库有独立 codeql.yml | 区分 ci.yml 本地回归范围和独立远端安全工作流，不声称远端检查已通过 |

## 5. 下一步计划

- **明确待办（Todo，由人类开发者确定后填写）**：本轮架构审查 P2 已完成，无新增获批功能任务；待用户决定是否提交并推送当前修改。
- **阻塞项 / 待确认疑点（Blockers）**：本轮实现无阻塞。真实权重/GPU 推理、视频站点下载、浏览器音频播放的人工验收尚未执行；因此不宣称完整产品端到端通过。Workspace 旧格式导入新 Workshop、插件市场/SDK 不属于本期范围。