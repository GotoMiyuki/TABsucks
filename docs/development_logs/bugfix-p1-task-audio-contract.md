# TABsucks 开发日志 · Bugfix-02 / vUnreleased

*日志日期：2026-09-23 | 编写人：Codex*

---

## 1. 上下文快照

- **分支 / Tag**：`bugfix/workshop-runtime-isolation-ci-baseline`；未创建新 Tag，本期修改尚未提交。
- **依赖变动**：否。仓库依赖声明未更改；本机为验证临时安装了 `requirements-base.txt`、pytest、pytest-asyncio 和 httpx，未写入项目环境。
- **关联文档/Issue**：[P1 修改指南](../p1-task-audio-modification-guide.md)、[开发指导](../development_guide.md)、[HTTP API](../HTTP_API.md)；正式 Issue 编号待补充。

---

## 2. 本期摘要

本期修复剩余两项 P1：后台任务缺少完整所有权与安全取消，以及音频缓冲形状、采样率和输入 IO 不一致。Kernel 新增进程内 TaskService，将同车间导入、分离、分析和关闭纳入统一排他与排空边界；业务终态改为结果和状态落盘后发布。音频在 RC 中统一为 float32、`(channels, samples)`、每缓冲采样率；模型插件入口按需转换单声道，stem WAV 以浮点格式保存。上传改为分块暂存，URL 下载和解码移出事件循环，并增加文件大小与解码内存预算检查。

验证结果：当前轻量环境中 531 项非 URL、非真实模型单测通过，JavaScript 3 项通过，Ruff CI correctness 规则通过；13 项测试被选择性排除，真实模型及联网路径仍待完整环境验证。

---

## 3. 核心内容详解

### 3.1 架构与设计决策

- **数据结构选型**：TaskService 持有 task ID、车间、类型、状态、阶段、错误及协程句柄，记录保留在进程内并限制历史长度；业务持久化仍由 Workshop/WorkshopCache 负责，不引入数据库或外部队列。
- **流程/状态机**：接单/去重 → 装载输入 → GPU 等待/插件执行 → committing → 持久化产物和 `state.json` → done/failed。取消转为 `cancelling`，同步 worker 必须真正退出才释放车间；`committing` 不接受取消。关闭/删除先标记停止接单，再排空当前任务，最后清理上下文。
- **接口/模块边界约定**：Orchestrator 执行插件并报告进度，Workshop 负责业务状态与唯一终态事件，TaskService 管理句柄和互斥。RC 的 `set_audio_buffer/get_audio_buffer` 是音频专用边界；通用 buffer 继续用于频谱等非音频数据。SSE 断线后可查询进程内任务，服务重启将持久化的运行中业务状态标记为 `interrupted`，不自动重跑。
- **IO 与内存边界**：上传按 1 MiB 分块，默认文件上限 512 MiB；解码前对可识别格式估算原音频、六轨和临时转换的 8 份 float32 内存，默认预算 1024 MiB，可环境变量调整。预算为提前拒绝策略，不是进程实际内存的硬限制。

### 3.2 关键代码实现详解

**改动点 A：TaskService 接单与取消**

- **意图**：避免相同请求启动两个协程、冲突请求覆盖状态，并防止重复取消打断正在等待退出的同步 worker。
- **关键代码**：

  ```python
  record, reused = tasks.admit(wid, "analysis", (stem_name, plugin_name, input_path))
  if reused:
      return record.handle
  # cancel() 对 cancelling 重入直接返回；committing 返回 409。
  ```

**改动点 B：音频缓冲契约**

- **意图**：在线分析和重启重载走相同的布局和采样率来源，避免把声道轴误当时间轴。
- **关键代码**：

  ```python
  rc.set_audio_buffer(stem_name, samples, sample_rate)
  audio, sr = rc.get_audio_buffer(stem_name)
  mono = to_mono(audio)  # 插件入口才转换，不改共享 stem
  ```

**改动点 C：成功终态**

- **意图**：不再在插件刚返回时提前发布 done。
- **关键代码**：

  ```python
  track_files = persist_separated_tracks(wid, task_id)
  workshop.complete_separation(track_files, task_id=task_id)  # 保存 state 后 emit done
  tasks.finish(record, "done")
  ```

---

## 4. 调试踩坑时间线

| 轮次 | 我的操作 / 触发条件 (Action) | 系统报错 / 观察结果 (Observation) | 最终修正决策 (Decision) |
| :--- | :--- | :--- | :--- |
| 1 | 安装基础依赖后收集全部相关测试 | 两个模型测试在收集阶段缺少 PyTorch | 不把缺依赖误报成代码失败；先执行无需模型的回归，真实模型验证列入阻塞项 |
| 2 | Windows 默认临时目录运行 pytest | `pytest-of-99662` 权限错误 | 将 `--basetemp` 指向当前工作区内专用目录 |
| 3 | 运行旧 HTTP 和编排测试 | 旧断言要求原文件名不带任务 ID，并假定默认分离器是示例插件 | 保留任务 ID 命名防覆盖；更新旧测试以明确选择示例插件并验证新文件名 |
| 4 | 审查取消与 HTTP 提交窗口 | 同步 FastAPI 路由可能跨线程调用 `Task.cancel()`；请求断开可能把已提交输入误报取消 | 取消路由改 async；提交阶段等待工作线程完成并返回实际提交结果 |

---

## 5. 下一步计划

- **[ ] 明确待办（Todo，由人类开发者确定后填写）**：在具备项目完整 CPU/GPU 依赖及模型权重的环境跑真实分离、和弦插件端到端验证；手工检查浏览器 SSE 断线、刷新恢复与关闭过程提示。
- **❗️ 阻塞项 / 待确认疑点（Blockers）**：当前轻量验证环境未安装 PyTorch/audio-separator，真实模型推理与其输出 padding 容差尚未验证；超长、未知元数据的压缩音频可能在解码阶段才暴露内存风险。正式 Issue/版本编号尚未指定。