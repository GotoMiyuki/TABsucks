# TABsucks 前端改造交付与验证记录

日期：2026-09-29  
需求基线：[前端开发改造方案 v1.0](frontend-redesign-plan.md)  
交付状态：本版范围内功能已实现，代码及可控样例回归完成；目标浏览器、系统主题动态切换和听感验收仍需发布前人工复核。

## 已交付能力

| 工作包 | 实现结果 |
|---|---|
| UI-01 / UI-02 | 欢迎、分离中、部分分析结果页面；系统深浅主题、Canvas 主题颜色、焦点与响应式布局 |
| UI-03 | 中文项目术语、主区域欢迎页、本地/链接/拖放导入、项目菜单、持久任务反馈与最近失败记录 |
| UI-04 | 未分析即可试听；原曲与分轨互斥；播放器按项目管理；面板切换保留位置与设置 |
| UI-05 | 六轨时间轴、逐轨分析、顺序批量分析、部分结果、音量/静音/独奏 |
| UI-06 | 实际分轨下载、有效轨道 MIDI 子集、历史结果选择、显示与导出使用同一结果标识 |
| UI-07 | LastTab 映射、项目会话恢复、重连核对、换源与重跑失效处理、过期响应隔离 |
| UI-08 | 0.5–2 倍速、A–B 循环、和弦点击定位、播放头跟随、空格播放暂停 |
| BE-01 / BE-02 | 原曲与分轨明确区分，缺失轨道 404；实际媒体类型、文件名、附件下载及 HEAD 验证 |
| BE-03 / BE-04 | 音源版本、有效历史结果列表及不透明结果标识；MIDI 校验同一结果、音源及处理状态 |
| QA-01 | 定向回归、浏览器主流程、深浅截图和同步采样；人工发布检查见下文 |

继续使用原生 JavaScript 模块和现有 Python 服务。未迁移项目目录、存储结构或结果文件格式。项目名、音源、分析选择、LastTab 和结果使用现有服务端保存；播放位置、混音、倍速、循环、缩放、跟随与结果查看选择仅在同次页面会话中按项目恢复。刷新、关闭或切换项目均不会自动播放。

## 代码与接口

- `src/ui/static/index.html`、`css/workbench.css`：统一工作台布局、主题、菜单与对话框。
- `js/app.js`、`ui.js`：项目与导入流程、中文反馈、串行位置保存、重连及异步隔离。
- `js/playback_controller.js`：媒体生命周期、混音、练习、结果选择与导出。
- `js/analysis_controller.js`、`separation_controller.js`：真实任务进度、取消、逐轨失败与批量队列。
- `src/ui/api/media.py`：实际音频、紧凑波形缓存、结果来源及导出一致性。

新增参数和字段的完整协议见 [HTTP API](HTTP_API.md)。旧合法请求继续可用；请求不存在的分轨明确失败，不能把原曲作为分轨响应。结果失效时返回 409，新界面要求核对刷新后的结果，不自动导出另一个模型。

## 自动回归

Windows 本机，Python 3.12.14、Node.js 24。使用项目已有环境，无模型下载或安装。

| 检查 | 结果 |
|---|---|
| 全项目非慢速、非联网 Python 测试 | 579 项通过、2 项跳过、5 项排除 |
| HTTP、任务/音频契约、架构、项目、MIDI、波形回归 | 178 项通过 |
| HTTP 最终路由调整后的复核 | 58 项通过 |
| JavaScript 模块共享状态、批量分析顺序、时间轴缩放及漂移补偿 | 6 项通过 |
| 所有前端模块语法检查 | 通过 |
| 修改的 Python 文件 Ruff 检查 | 通过 |
| 修改内容空白检查 | 通过 |

重点验证缺失分轨不回退原曲、实际音频下载内容与文件名、HEAD、音源版本失效、历史结果选择、对应 MIDI 音高、失效结果 409、引用数量校验及处理中拒绝导出。JavaScript 夹具更新为当前资源版本，保留既有业务断言。

## 浏览器验证

在 Codex 内置 Chromium 浏览器、Windows 本机执行。测试使用独立 `.workbench-preview*` 数据目录，没有改动用户缓存。样例为实际生成的 8 kHz WAV，长度三分钟和十五分钟，六条等长分轨；可控分离/分析插件经现有 HTTP、任务编排和事件推送运行。可控插件仅由预览脚本注册，不改变正式模型注册。

已执行：

- 导入本地文件、分离六轨、不经分析试听、音轨下载入口与 MIDI 子集导出。
- 一轨分析失败、一轨成功；失败记录持续可见，成功轨仍可导出；重新分析与取消。
- 同轨多个历史结果查看；更改查看结果时等待加载完成后才能导出。
- 原曲/分轨切换、倍速、音量、静音、独奏、循环、定位、同项目面板切换。
- 项目重命名、长名称、省略显示、关闭与重新打开、刷新后的暂停恢复。
- 二十次关闭/打开项目：关闭后 0 个音频元素，重开后 7 个元素且全部暂停，未累积旧音源。数据见 [lifecycle-samples.json](workbench-validation/lifecycle-samples.json)。此项不替代浏览器堆内存及事件连接的人工性能检查。
- 断线提示、重连状态核对；旧任务失败不会覆盖后续成功。
- 1440×900、1366×768、1024×768 的布局检查：页面未产生整体横向溢出，底部播放控件可达。
- 键盘操作导入按钮、菜单和对话框；取消对话框不启动重跑。
- 系统深色页面及预览脚本模拟的浅色页面。

浅色截图使用预览专用样式模拟，正式 UI 始终使用系统 `prefers-color-scheme`；未改变系统主题。当前工具仅连接内置浏览器，因此不把这些结果记作 Edge / Chrome 稳定版验收。

## 多轨同步记录

以音频元素实际 `currentTime` 采样，取六条活动分轨中最大与最小时间之差，作为任意轨相对主轨误差的上界；播放头没有使用动画累计时间模拟。各音轨经同一个 Web Audio 输出时钟混音，继续使用媒体流，不全量解码长音频为浏览器 AudioBuffer。播放时每 25ms 核对媒体时间，小偏差采用有界速率补偿，大于 150ms 的突变才重新定位。从轨暂时补偿幅度不超过当前倍速的 8%，对齐后恢复所选倍速，保留音高。项目销毁时断开音频节点并关闭输出上下文。

初轮 1.5 倍速测量出现一次 59.618ms 的短暂差值，超过 50ms 目标，原始数据保留在 [sync-before-correction.json](workbench-validation/sync-before-correction.json)。因此替换小偏差反复定位的方式，并用独立算法测试验证正负偏差在 0.75、1、1.5 倍速下收敛；最终浏览器复测数据单独记录。

仅速率补偿仍观察到最后一轨短时落后，原始数据见 [sync-rate-only.json](workbench-validation/sync-rate-only.json)。加入统一输出时钟后重新执行完整三分钟测试，以最终数据为准。

最终同步数据见 [sync-samples.json](workbench-validation/sync-samples.json)，最终循环数据见 [loop-final-samples.json](workbench-validation/loop-final-samples.json)。采样记录保留实际时间戳、各轨媒体时间和暂停状态。三档速度各从 0 到 180 秒每 30 秒采样一次，共 21 个采样点，六轨在所有采样点均持续播放。

| 倍速 | 连续播放时间 | 采样点数 | 最大轨间差 | 50ms 目标 |
|---|---|---|---|---|
| 0.75× | 180 秒 | 7 | 0.023ms | 通过 |
| 1× | 180 秒 | 7 | 0.027ms | 通过 |
| 1.5× | 180 秒 | 7 | 5.333ms | 通过 |

循环使用 A=12s、B=17s，连续十次，每次检测到回跳后等待约 200ms 采样。统一输出时钟后的复测十次均继续播放，最大轨间时间差约 0.047ms，低于 50ms 目标。此数字反映浏览器时间属性及其精度，不能作为音频采样级无缝循环或听感的结论。早期循环样本保留在 `loop-samples.json`。

## 截图

| 状态 | 文件 |
|---|---|
| 深色欢迎页 | [welcome-dark.png](workbench-validation/welcome-dark.png) |
| 浅色欢迎页 | [welcome-light.png](workbench-validation/welcome-light.png) |
| 深色分离中 | [separating-dark.png](workbench-validation/separating-dark.png) |
| 浅色分离中 | [separating-light.png](workbench-validation/separating-light.png) |
| 深色部分分析完成/失败 | [partial-analysis-dark.png](workbench-validation/partial-analysis-dark.png) |
| 浅色部分分析完成/失败 | [partial-analysis-light.png](workbench-validation/partial-analysis-light.png) |
| 深色工作台 | [workbench-dark.png](workbench-validation/workbench-dark.png) |
| 浅色工作台 | [workbench-light.png](workbench-validation/workbench-light.png) |
| MIDI 子集选择 | [midi-subset.png](workbench-validation/midi-subset.png) |

## 运行与发布检查

正式应用沿用 README 的启动方式；前端和 Python 服务需要一起更新并重启，静态资源版本为 `20260929v1`。可以使用隔离预览检查界面：

本次交付保留的隔离预览地址为 `http://127.0.0.1:8017/`，使用三分钟生成音频，页面按系统主题显示；其他测试服务已停止。该预览依赖当前运行进程，关闭进程后可用下方命令重新启动。

```powershell
.venv\Scripts\python.exe scripts/workbench_preview.py --seed --demo-jobs --port 8017 --cache-root .workbench-preview-review
```

预览还支持 `--seconds 900` 和 `--theme light`。示例模型产生可控结果，不能代表正式模型的识别质量。

发布前仍需人工完成方案中以下门槛：

1. Windows Edge / Chrome 稳定版各完整主流程并记录版本；125% 系统显示缩放。
2. 播放中改变实际系统深浅色，确认页面、Canvas 和原生控件同步且音频不中断。
3. 真实歌曲及正式模型的一次闭环；耳听倍速、混音与十次循环，排除可辨持续错拍。
4. 同设备改造前后的长音频首次波形加载、内存和性能比较，以及事件连接/动画资源检查；当前无改造前运行基准，不作性能提升百分比承诺。

这些人工项尚未完成，不据此宣布全部发布验收门槛已通过。代码回退需前后端同时匹配；不删除或迁移用户项目文件。
