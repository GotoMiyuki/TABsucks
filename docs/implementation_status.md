# 当前实现状态与状态文件版本

核对日期：2026-09-26。本文与 [当前架构](架构设计.md)、[HTTP API](HTTP_API.md) 是现行说明；会议记录和历史开发日志用于追溯，不覆盖当前接口。

## 架构审查 P2 收敛结果

本次 P2 指整体架构审查中的维护性和持久化问题，不指 `plan.md` 中插件市场、SDK 等产品路线图 P2。

| 审查问题 | 当前实现与边界 |
|---|---|
| Workshop 单文件过大 | 状态模型 `workshop_state.py`、单车间运行时 `music_workshop.py`、集合生命周期 `workshop_manager.py`；`workshop.py` 仅兼容重导出 |
| Kernel 混合任务细节 | Kernel 保留启动、生命周期、任务入口；组合服务 `workshop_jobs.py` 管理装载、监督、结果提交 |
| API 混合多项职责 | `analysis.py` 管理任务路由并聚合子路由；`uploads.py` 管输入；`media.py` 管可视化、音频及 MIDI；`dependencies.py` 管共享依赖 |
| 前端单文件过大 | `app.js` 管装配、车间导航和输入；共享状态在 `app_state.js`；分离、分析、播放各有控制器，使用显式回调避免循环导入 |
| CSS 难维护 | `style.css` 按原始顺序导入 base / workflow / playback / overrides，保持原级联关系 |
| 两套 EventBus | 只保留 `core/event_bus.py` 的线程安全订阅队列实现；`kernel.py` 重导出同一个类 |
| 旧 Workspace / `_s` 包装 | Workspace 隔离到 `kernel/legacy`，旧入口仅兼容；`_s` 是正式实现的类别名 |
| 旧分离、伪 MIDI、闲置 UI | 删除旧分离器、两个 `.deprecated` 插件副本及未引用的 Mixer/Timeline；旧音频转 MIDI API 明确失败，正式和弦导出仍生成标准 MIDI |
| state.json 无版本 | 增加 SchemaVersion 与纯迁移函数、迁移前校验和备份、原子写盘及未来版本保护 |

兼容入口不应被新业务模块引用。保留 Workspace 兼容行为不是把旧文件格式自动导入新车间；两者模型不同。已删除代码可从 Git 历史恢复，不是用户缓存数据删除。

## 状态版本与恢复规则

根字段 `SchemaVersion` 是非负整数（不接受 bool、浮点数、字符串或 null），当前为 1。省略时认作旧 Workshop v0；v0 → v1 保持原 Tab 结构，补显式版本号。迁移函数深拷贝输入，可重复调用，不访问磁盘。

加载顺序：读 JSON → 检查版本、嵌套形状及业务字段 → 将遗留 running 改为 interrupted → 旧版本备份 → 原子写入当前版本 → 注册运行时。

- 原始字节保存在同车间目录 `state.pre-v1.json.bak`；已有文件不覆盖，内容必须与待迁移原文件一致。若不一致（包括不完整备份），跳过车间并报告错误，人工核对后再处理。
- 不支持的未来版本、非法字段、损坏 JSON、无法备份或无法提交都不会覆盖原 `state.json`。失败仅影响该车间，其他车间继续加载。
- 写盘仍使用 `WorkshopCache.save_state` 的临时文件 + 原子替换；失败后可重试，已有正确备份复用。
- 当前版本且无运行中任务时不重写状态；不重新执行被中断任务。
- 恢复备份前先关闭应用，保留当前状态副本，再把确认过的备份复制回 `state.json`。再次启动会重新迁移；不要直接删除未知内容的备份。
- 未来增加 schema 时须递增版本、添加明确的逐版本迁移和测试；不允许仅调整反序列化默认值来冒充迁移。

## 功能与验证边界

| 能力 | 实现状态 | 本轮验证 |
|---|---|---|
| 车间状态/任务隔离/取消/音频契约 | 正式主路径 | 非联网 Python 回归与恢复专项测试 |
| 分离、逐轨和弦分析 | manifest 插件主路径 | 接口、适配器与模拟执行测试；未运行真实权重 GPU 推理 |
| 浏览器控制器与任务序列 | 原生 ES modules，HTTP + SSE | 真实模块图初始化、批量分析序列、缩放计算的 Node 测试；未做浏览器视听人工验收 |
| 可视化与 MIDI | 真实音频波形、已有分析结果、和弦区间 MIDI | HTTP / 单测；无数据返回空内容，不生成随机波形或假节拍 |
| AnalysisEngine 整体流水线 | 保留但未接 UI 主路径 | 单元测试，不宣称产品端到端完成 |
| 模型下载/视频站点下载 | 依赖网络及外部服务 | 本轮 slow/network 测试未运行 |

本轮没有新增依赖，也没有改动用户配置的环境约束文件。具体执行结果及命令见 [P2 开发日志](development_logs/bugfix-p2-modularity-state-schema.md)。
