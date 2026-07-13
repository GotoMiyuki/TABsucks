# 开发日志：UI 表现层（FR-18）

**日期：** 2026-06-20
**涉及模块：**
1. `src/ui/server.py`
2. `src/ui/api/workshops.py`
3. `src/ui/api/analysis.py`
4. `src/ui/api/events.py`
5. `src/ui/static/index.html`
6. `src/ui/static/css/style.css`
7. `src/ui/static/js/app.js`
8. `src/ui/static/js/api.js`
9. `src/ui/static/js/waveform.js`
10. `src/ui/static/js/event_stream.js`
11. `src/ui/mock_data/demo.json`
12. `src/kernel/core/event_bus.py`
13. `docs/ui_mock_swap.md`
**依赖项：** `fastapi`, `uvicorn[standard]`, `python-multipart`

---

## 一、模块概述

UI 表现层是 TABsucks 的用户交互入口，负责音频上传、音轨分离进度展示、逐轨分析配置、以及分析结果的时间轴可视化与播放。

### 核心设计原则

- **全 Mock 模式**：当前所有后端操作（分离/分析）走模拟路径，API 路由骨架保留，后续只需替换 `analysis.py` 中的 4 个函数即可接入真实后端
- **暗黑线框风格**：黑底 + 白色实线边框 + 蓝色胶囊按钮（`#5b65ff`），JetBrains Mono 字体
- **三步导航**：INPUT → SELECT（含 separate / analyze 两阶段）→ OUTPUT
- **SSE 实时推送**：通过 `EventBus` + `EventSource` 实现分离/分析进度的实时更新
- **比例值渲染**：后端输出 0-1 比例数据，前端 Canvas 自行计算像素位置（与 `src/visualizer/` 设计一致）

---

## 二、架构设计

### 2.1 整体分层

```
浏览器 (HTML/CSS/JS)
    │  fetch / EventSource
    ▼
FastAPI 服务器 (src/ui/server.py)
    │
    ├── /api/workshops/*   → workshops.py  → WorkspaceManager (真实)
    ├── /api/workshops/*/separate  → analysis.py → Mock 分离
    ├── /api/workshops/*/analyze   → analysis.py → Mock 分析
    ├── /api/workshops/*/visualization → analysis.py → demo.json
    └── /api/workshops/*/events    → events.py → EventBus → SSE
```

### 2.2 前端模块结构

```
app.js           主控制器：状态管理、步骤导航、DOM 操作
├── api.js       API 封装层：所有 fetch 调用集中于此
├── event_stream.js  SSE 封装：自动断开重连
├── waveform.js  Canvas 渲染：波形、时间轴（和弦+节拍）、播放头
└── (timeline/mixer 已合并进 app.js)
```

### 2.3 事件总线

`src/kernel/core/event_bus.py` 实现了会议记录 §5.2 的设计：

- `WorkshopEvent`：不可变事件对象（workshop_id, type, payload, emitted_at）
- `EventBus`：进程级订阅/发布，基于 `asyncio.Queue`
- 全局单例 `bus` 供 FastAPI 路由和模拟任务共享

**当前支持的事件类型：**

| 事件 | 触发时机 | 前端反应 |
|------|---------|---------|
| `separation_progress` | 分离进行中 | 进度环/进度条更新 |
| `separation_done` | 分离完成 | 进度环变绿、next 按钮亮起、构建音轨选择网格 |
| `analysis_started` | 单轨分析开始 | 状态标签 → running |
| `analysis_done` | 单轨分析完成 | 状态标签 → done、检查是否全部完成 |

---

## 三、页面流程

### 3.1 INPUT（Step 1）

- 两个并排白色边框矩形按钮：`Upload audio file` / `Fetch from URL`
- 上传后立即触发分离，左侧出现 SVG 进度环
- 分离完成 → 进度环变绿显示 ✓ → next 按钮亮起
- 音频信息以 tag 形式展示（文件名、时长、采样率）

### 3.2 SELECT / separate（Step 2 Phase 1）

- 右上角 `MODEL` 按钮（占位，未来弹出模型选择）
- 2×3 方形网格，每个音轨一个白色边框大方块（BASS / GUITAR / VOCAL / KEYBOARD / DRUM / ELSE）
- 默认全选，点击切换选中状态（蓝色边框 + 勾选标记）
- 点 next 进入 analyze 阶段

### 3.3 SELECT / analyze（Step 2 Phase 2）

- 仅显示已选中的音轨
- 每行：色块 + 轨名 + 模型下拉 + run 按钮 + 状态标签
- 鼓音轨：仅显示 `Deep Rhythm`
- 其他音轨：`ChordNet` + `BTC-SL` + `Deep Rhythm`
- 底部 `run all` 按钮一键触发全部分析
- 全部完成 → output 按钮亮起

### 3.4 OUTPUT（Step 3）

- 外层白色边框容器，内部纵向排列多条音轨行
- 每行结构：左侧标签区（轨名纵向 + 音量滑块）+ 右侧内容区（波形 Canvas + 和弦色块条）
- 所有 Canvas 共享红色播放头，同步播放
- 底部操作栏：previous / 播放控制（进度条 + 时间 + 圆形播放按钮 + 倍速）/ next

---

## 四、关键设计决策

### 4.1 为什么用纯 HTML/CSS/JS 而非 React/Vue

MVP 阶段目标是快速出演示，不引入构建工具链。FastAPI 直接托管静态文件，`localhost:8000` 即可访问。远期演进路径：HTML/CSS → Tauri/Vite + React/Vue（见 `docs/ui_mock_swap.md`）。

### 4.2 为什么分离在 INPUT 步骤触发而非 SELECT

文档规范要求上传后进度环出现在 Upload 按钮左侧，且 next 亮起条件为"分离完成"。因此分离必须在 INPUT 步骤就开始，而非进入 SELECT 后。

### 4.3 为什么 SELECT 分为两个阶段

会议记录 §4 明确了"音乐车间"的四 Tab 流水线。UI 排版规范将 Tab2+Tab3 合并为 SELECT 导航步骤，但内部仍保留 separate → analyze 的两阶段逻辑，通过 next/previous 按钮在子阶段间导航。

### 4.4 EventBus 的全局单例 vs 依赖注入

MVP 阶段使用全局单例 `bus`，路由模块和模拟任务直接 `from src.kernel.core.event_bus import bus`。未来多进程场景需改为依赖注入（FastAPI 的 `Depends`）。

### 4.5 Canvas 渲染 vs DOM 渲染

波形和时间轴使用 Canvas 而非 DOM 元素，原因：
- 峰值数组 2000+ 个点，DOM 渲染性能不可接受
- 与远期 DAW 级渲染目标一致（架构设计文档明确要求 Canvas/WebGL）
- `waveform.js` 的 `drawTimeline()` 函数已支持和弦色块、节拍线、播放头的统一绘制

---

## 五、Mock → 真实替换清单

详见 `docs/ui_mock_swap.md`。核心替换点：

| 替换点 | 文件 | 函数 | 当前 | 目标 |
|--------|------|------|------|------|
| A | `analysis.py` | `_run_mock_separation()` | asyncio 模拟进度 | `Separator.separate()` + EventBus |
| B | `analysis.py` | `_run_mock_analysis()` | asyncio 模拟 + 预置数据 | `AnalysisEngine.run_single()` |
| C | `analysis.py` | `get_visualization()` | 返回 `demo.json` | `export_visualization_json()` |
| D | `analysis.py` | `get_audio()` | 生成测试正弦波 | numpy→WAV 字节流 |

**无需改动的部分：** `workshops.py`（已用真实 WorkspaceManager）、`events.py`（真实 SSE）、`event_bus.py`（真实事件总线）、全部前端 JS/CSS/HTML。

---

## 六、文件清单

### 新建文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/kernel/core/event_bus.py` | ~58 | 事件总线 |
| `src/ui/server.py` | ~29 | FastAPI 入口 |
| `src/ui/api/workshops.py` | ~70 | 车间 CRUD |
| `src/ui/api/analysis.py` | ~170 | 分离/分析/可视化/音频（Mock） |
| `src/ui/api/events.py` | ~28 | SSE 端点 |
| `src/ui/static/index.html` | ~105 | 单页 HTML |
| `src/ui/static/css/style.css` | ~380 | 暗黑线框风格 |
| `src/ui/static/js/app.js` | ~520 | 主控制器 |
| `src/ui/static/js/api.js` | ~75 | API 封装 |
| `src/ui/static/js/waveform.js` | ~145 | Canvas 渲染 |
| `src/ui/static/js/event_stream.js` | ~40 | SSE 封装 |
| `src/ui/mock_data/demo.json` | ~2000 peaks | 预置可视化数据 |
| `docs/ui_mock_swap.md` | ~120 | Mock→真实替换清单 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `requirements.txt` | 添加 `fastapi`, `uvicorn[standard]`, `python-multipart` |
| `src/kernel/core/workspace.py` | 将 `separator` 导入移入 `TYPE_CHECKING`，消除启动时对 ML 库的依赖 |

---

## 七、已知限制与后续工作

### 当前限制

1. **全 Mock**：分离和分析结果为模拟数据，不调用真实模型
2. **无持久化**：车间状态仅存内存，重启丢失
3. **播放为假**：播放头动画基于 `requestAnimationFrame` 时间推进，不播放真实音频
4. **无响应式**：最小宽度 1024px，未适配移动端
5. **速度切换为单向循环**：点击倍速标签循环切换，无下拉菜单

### 后续开发路径

1. **接入真实后端**：按 `docs/ui_mock_swap.md` 替换 4 个函数
2. **播放真实音频**：浏览器 `Audio` API 播放分离后的 WAV，同步播放头
3. **state.json 持久化**：车间状态写盘，冷启动恢复
4. **Canvas 交互增强**：拖拽 seek、滚轮缩放、hover 时间提示
5. **远期演进**：HTML/CSS → Tauri/Vite + React/Vue + Canvas/WebGL 渲染引擎

---

## 八、启动方式

```bash
cd ..\tabsucks
# 输入 TABsucks 在文件夹中的位置
py -m uvicorn src.ui.server:app --port 8000
# 浏览器访问 http://localhost:8000
```

依赖安装：
```bash
py -m pip install fastapi "uvicorn[standard]" python-multipart
```
