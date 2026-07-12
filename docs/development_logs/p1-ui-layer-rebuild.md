# 开发日志：UI 层重构 — 接入 Kernel + SSE 事件总线

> 本文是 [p1-ui-layer.md](p1-ui-layer.md) 的**重构延续**。旧的 mock-only UI 已
> 替换为基于 `Kernel + EventBus + SSE` 的真实现层，**接上 [p1-workshop-cache-system.md](p1-workshop-cache-system.md) 落地的车间系统**。

**日期：** 2026-07-12
**涉及模块：**
1. [src/ui/server.py](../../src/ui/server.py)（重写） — `make_app(kernel)` 工厂
2. [src/ui/api/workshops.py](../../src/ui/api/workshops.py)（重写） — `request.app.state.kernel` 模式
3. [src/ui/api/events.py](../../src/ui/api/events.py)（重写） — SSE 流
4. [src/ui/api/analysis.py](../../src/ui/api/analysis.py)（重写） — upload **真实现** + 其余 mock 标 A/B/C/D
5. [src/ui/cli.py](../../src/ui/cli.py)（**新增**） — `python -m src.ui` 入口
6. [src/ui/static/index.html](../../src/ui/static/index.html)（改） — 欢迎面板 + × 关闭按钮 + 永久删除按钮
7. [src/ui/static/js/app.js](../../src/ui/static/js/app.js)（重写） — close / delete / switch 三套流程
8. [src/ui/static/js/api.js](../../src/ui/static/js/api.js)（重写） — error 兼容、SSE、close/delete 分离
9. [src/ui/static/js/event_stream.js](../../src/ui/static/js/event_stream.js)（小调） — connect 不再按 wid
10. [tests/unit/test_http_server.py](../../tests/unit/test_http_server.py)（**新增**） — 27 个测试
11. [docs/HTTP_API.md](../../HTTP_API.md)（**新增**） — API 文档

**依赖项：** `fastapi`, `uvicorn[standard]`, `python-multipart`, `httpx`（仅测试）

---

## 一、模块概述

UI 层是 TABsucks 进程的最外层，把 [Kernel + Workshop + EventBus](../../src/kernel/kernel.py) 暴露给浏览器。它做三件事：

* **HTTP REST 接口**：车间 CRUD / 上传 / 分离 / 分析 / 可视化
* **SSE 推送**：把 EventBus 上 `*` 事件实时推到浏览器
* **静态资源**：单页 `index.html` + 模块化 JS

### 核心设计原则

| 原则 | 落地 |
|------|------|
| **Kernel 工厂注入**：`make_app(kernel)` 而非模块级单例 | `app.state.kernel = kernel` |
| **路由从 app.state 拿实例**：每个请求 `request.app.state.kernel` | 不依赖全局 |
| **SSE 不按 wid 过滤**：客户端按 `event.workshop_id` 自过滤 | EventBus 简单 |
| **Mock 模式保留**：4 个 `[MOCK]` 替换点显式标注 | 接真插件时按字母查 |
| **UI 锁**：关闭/切换前 UI 必须 disable 控件 | `setControlsDisabled(true)` 然后调 API |

---

## 二、架构设计

### 2.1 整体分层

```
浏览器 (HTML/JS)
    │
    ├── fetch  ─── HTTP ──→  src/ui/server.py::make_app()
    │                            │
    │                            ├─ /api/workshops/*        → workshops.py
    │                            ├─ /api/events (SSE)       → events.py
    │                            ├─ /api/*/upload/sep/...   → analysis.py (mock B/C/D)
    │                            │        ↑
    │                            │        └─ 上传真实现
    │                            ▼
    │                        request.app.state.kernel
    │                            │
    │                            ├─ kernel.manager  ──→ MusicWorkshop (车间)
    │                            ├─ kernel.bus       ──→ 推给 SSE / 业务事件
    │                            └─ kernel.manager.close() / switch() / ...
    │
    └── EventSource(name="/api/events") ←──── SSE data: {type, payload, workshop_id, emitted_at}
```

### 2.2 文件结构

```
src/ui/
├── cli.py                    ← 新：CLI 入口（uvicorn 配置）
├── server.py                 ← 重写：make_app() 工厂
├── api/
│   ├── workshops.py          ← 重写：用 request.app.state.kernel
│   ├── events.py             ← 重写：SSE 工厂模式
│   └── analysis.py           ← 重写：upload 真实现 + [MOCK] A/B/C/D
├── static/
│   ├── index.html            ← 改：欢迎面板 + × 关闭
│   ├── css/style.css         ← 不动（待 UI 团队补全 welcome/busy 样式）
│   └── js/
│       ├── app.js            ← 重写：close/delete/switch 三套流程
│       ├── api.js            ← 重写：error 兼容、close/delete
│       └── event_stream.js   ← 小调：connect 不再按 wid
└── mock_data/demo.json       ← 不动

tests/unit/test_http_server.py   ← 新：27 测试（含真 uvicorn SSE smoke）
docs/HTTP_API.md                 ← 新：完整 API 文档
```

### 2.3 前后端契约（UI ↔ Workshop）

| UI 端 | 后端 |
|-------|------|
| 点关闭 → `setBusy(true)` + `setControlsDisabled(true)` | WorkshopManager.close() → save + 停 autosave，**MusicWorkshop 仍在 _workshops** |
| 点删除 → 二次确认 dialog | WorkshopManager.delete() → 内存 + 磁盘删（可 keep_state）|
| 点切换 → `setBusy(true)` | WorkshopManager.switch_to() → close 旧 + resume_autosave 新 |
| 上传文件 → FormData | set_raw_audio_from_bytes → cache/raw_audio/ + 自动命名（仅当 name="New Workshop"）|

**关键不变量**：inactive 车间前端不可写（UI 应 disable）；关闭 ≠ 删除；列表始终显示所有车间。

---

## 三、核心 API 接口表

### 3.1 车间管理（`/api/workshops*`）

| Method | Path | 行为 | 响应 |
|--------|------|------|------|
| GET | `/api/workshops` | 列表（含已关闭 / inactive）| 200 + `[{id, name, last_tab, active}]` |
| POST | `/api/workshops` | 创建（deactivate 旧 active）| 201 + `{id, name, last_tab}` |
| GET | `/api/workshops/{wid}` | 完整 state.json | 200 / 404 |
| PUT | `/api/workshops/{wid}` | 重命名 | 200 `{ok, name}` / 404 |
| POST | `/api/workshops/{wid}/close` | 关闭 = deactivate | 200 `{ok, active_id}` / 404 |
| POST | `/api/workshops/{wid}/switch` | 切换 active | 200 `{ok, active_id}` / 404 |
| GET | `/api/workshops-active` | 当前 active id（null = 欢迎页）| 200 `{active_id}` |
| DELETE | `/api/workshops/{wid}?keep_state={bool}` | 永久删除 | 200 / 404 |

### 3.2 流程（`/api/workshops/{wid}/*`）

| Method | Path | 行为 | 响应 | 状态 |
|--------|------|------|------|------|
| POST | `/upload` | multipart 上传 | 200 `{ok, filename, name, rel_path}` | **真实现** |
| POST | `/separate` | 触发分离 | 200 `{ok, message}` | `[MOCK] A` |
| POST | `/analyze` | 触发单轨分析 | 200 `{ok, message}` | `[MOCK] B` |
| GET | `/visualization?track=name` | 可视化 JSON | 200 dict | `[MOCK] C` |
| GET | `/audio/{track}` | 返回 wav bytes | 200 audio/wav | `[MOCK] D` |

### 3.3 SSE（`/api/events`）

```
GET /api/events
  Response: text/event-stream
  Message 数据: {"type": "...", "payload": {...}, "workshop_id": "...", "emitted_at": float}
```

服务端**不按 workshop_id 过滤**，前端按需过滤。

事件类型见 [HTTP_API.md](../../HTTP_API.md#sse-事件流) 或 `src/kernel/kernel.py::EventType`。

---

## 四、关键设计决策

### 4.1 为什么用 `make_app(kernel)` 工厂而非模块级 singleton

**旧版本**（已被弃用）：

```python
# src/ui/api/workshops.py  (旧)
from src.kernel.core.workspace import WorkspaceManager
wm = WorkspaceManager()    # ← 模块级，进程启动即创建
wm.create("Demo Workshop") # ← 启动时创建，违反"F5 不激活"原则
```

**问题**：
1. 测试时 `pytest` 在同一进程跑多个测试 → 全局 state 串扰
2. 与新 `Kernel` 集成不起来（Kernel 包含更多装配）
3. "Demo Workshop" 违反会议 §4 的"启动时不在任何车间状态"

**新版本**：

```python
# src/ui/server.py  (新)
def make_app(kernel: Kernel) -> FastAPI:
    app = FastAPI()
    app.state.kernel = kernel
    ...
    return app
```

```python
# src/ui/cli.py
kernel = Kernel(cache_root=Path("cache")).boot()
uvicorn.run(make_app(kernel), port=8000)
```

**收益**：
- 测试用 `TestClient(make_app(Kernel(tmp_path)))` 完全隔离
- Kernel 装配在 CLI 层（顶级入口），HTTP server 只是个协议层
- 未来想换 ASGI 框架 / gRPC / WebSocket 都不影响业务

### 4.2 为什么 SSE 在服务端不做 wid 过滤

**选项**：

| A. 全局广播（当前） | B. 按 wid 订阅 |
|---|---|
| EventBus 一对多 publish | EventBus 维护 per-wid subscriber list |
| 前端按 `event.workshop_id` 过滤 | 服务端发送时已过滤 |
| EventBus 极简 | EventBus 复杂，但传输带宽省 |

**理由（选 A）**：
- MVP 阶段事件量小（一次分离 ~5 个 progress + 1 个 done），带宽可忽略
- EventBus 单元测试简单（只要看 Queue 有没有收到）
- 前端 SSE 接一次连接永久用，不用做"换车间重建连接"的复杂逻辑
- 后期流量上来再迁移到方案 B

前端按 `event.workshop_id` 过滤示例（已实现 `event_stream.js`）：

```javascript
const handlers = {
    separation_done: () => refreshStemGrid(),
    analysis_done: (p) => p.track === currentTrack ? showResult(p) : null,
    // 通用 wrapper：过滤掉非当前车间的杂音
};
```

### 4.3 为什么 `BackgroundTasks` 而不是 `asyncio.create_task`

**事故**：

```python
# 旧分析.py
async def trigger():
    asyncio.create_task(_run_mock_separation(...))   # ← 这里在同步 endpoint 里崩
```

报错：`RuntimeError: no running event loop`（FastAPI sync 端点里没有 loop）。

**修复**：用 FastAPI 提供的 `BackgroundTasks`（与 endpoint 同 lifecycle，请求结束才跑 task）：

```python
@router.post("/separate")
def trigger(background: BackgroundTasks, ...):
    background.add_task(_run_mock_separation, wid, model, bus)
    return {"ok": True}
```

**收益**：
- task 在响应返回后才启动（不阻塞 API 返回）
- 不依赖 event loop 已存在
- 测试时 `TestClient` 自动跑 background tasks 直到完成

### 4.4 为什么 close / delete 是两个 API

**讨论背景**：会议 §4 + 后续讨论明确"关闭 ≠ 删除"：
- **close = deactivate**：保存数据 + 停后台线程，但 MusicWorkshop 实例不释放（< 2KB），
  列表里仍可见，下次点同一项直接复用
- **delete = 永久**：内存 + 磁盘清理，可选备份 state.json

**API 设计**：两个端点不同语义、不同返回值：

| 端点 | 副作用 | active_id 影响 |
|------|--------|---------------|
| `POST /close` | 不删数据，MusicWorkshop 保留 | 若 wid 是 active → `null` |
| `DELETE /workshops/{wid}` | 删内存 + 磁盘 | 若 wid 是 active → `null`，列表里少一项 |

如果揉成一个端点 + query param，**前端语义不清**且容易误用（少写一个 query 就误删）。

### 4.5 错误响应统一格式

```http
HTTP 404 Not Found
{"detail": {"error": "车间 abc12345 不存在"}}
```

统一用 `{"detail": {"error": msg}}` 而非默认 FastAPI 的 `{"detail": msg}`，让前端判断更直接：

```javascript
if (!r.ok) showToast(`失败: ${r.error}`);   // r.error 有具体消息
```

`detail` 这层外裹是 FastAPI 必需，自定义错误码都在 `detail.error` 内。

### 4.6 为什么 upload 路径要单独实现

旧版 `upload_audio` 直接 `ws.audio_path = file.filename` 不写盘——意味着：
- 原始音频"消失"（Python 局部变量，重启就丢）
- 分离模型若需要原文件，无法访问

新版本调 `set_raw_audio_from_bytes(data, filename)`：

```python
abs_path = ws.set_raw_audio_from_bytes(content, dst_name)
# → cache/raw_audio/<dst_name> 落盘
# → state.json 加 RawAudioFilePath
# → 自动命名（仅当 WorkshopName==="New Workshop"）
# → emit raw_audio_set 事件
# → 立即 save（关键路径）
```

完全等价于"先下载 URL 到本地，再走 set_raw_audio"。

---

## 五、函数详解

### 5.1 `make_app(kernel)`

构造 FastAPI app，挂载 3 个 router + 静态资源 + 根路由。

```python
def make_app(kernel: Kernel) -> FastAPI:
    app = FastAPI(title="TABsucks", version="0.2.0")
    app.state.kernel = kernel
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(workshops_router, prefix="/api", tags=["workshops"])
    app.include_router(events_router, prefix="/api", tags=["events"])
    app.include_router(analysis_router, prefix="/api", tags=["analysis"])

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
```

### 5.2 SSE Generator（`events.py`）

```python
@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    kernel = ...  # request.app.state.kernel
    q = kernel.bus.subscribe()

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = q.get(timeout=0.3)   # 0.3s 超时让 disconnected 检查生效
                except Empty:
                    continue
                yield f"data: {json.dumps(...)}\n\n"
        finally:
            kernel.bus.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream", ...)
```

关键点：
- **finally unsubscribe**：浏览器断开时 `request.is_disconnected()` 触发，确保 Queue 不泄漏
- **0.3s poll 而非 `await q.get()`**：后者在 Queue 空时无限阻塞，disconnected 检测不及时
- **`X-Accel-Buffering: no`**：告诉 nginx 别缓冲，立即推到客户端

### 5.3 Workshop 自动激活（`/api/workshops` POST）

```python
def create_workshop(req, request):
    kernel = _kernel(request)
    info = kernel.create_workshop(name=req.name)
    # kernel.create_workshop 内部：
    #   1. close 当前 active（save + 停 autosave）
    #   2. 新建车间 + 立即 save 空 state.json
    #   3. 加到 _workshops
    #   4. ws.resume_autosave()  ← 启动新车间后台线程
    #   5. active_id = 新车间 id
    return info
```

后端职责已落到 Kernel，前端只要 `await api.createWorkshop(name)` 然后 `refreshList()` 即可。

### 5.4 UI disable 流程（`app.js`）

```javascript
async function handleSwitchWorkshop(wid) {
    if (state.busy) return;
    state.busy = true;
    setControlsDisabled(true);         // 立即锁
    document.body.classList.add('busy'); // CSS 类用于显示转圈
    const r = await api.switchWorkshop(wid);
    if (!r.ok) {
        state.busy = false;
        setControlsDisabled(false);    // 失败回滚
        document.body.classList.remove('busy');
        showToast(`切换失败: ${r.error}`, 'error');
        return;
    }
    state.currentWid = wid;
    await refreshWorkshopList();
    await loadActiveWorkshopData();
    state.busy = false;
    setControlsDisabled(false);
    document.body.classList.remove('busy');
}
```

**关键**：UI 锁必须 **先于 API 调用**，否则用户在 switch 进行中改音量可能丢失。

---

## 六、单元测试

`tests/unit/test_http_server.py` 提供 27 个测试：

| 类 | 测什么 |
|-----|--------|
| `TestRoot` | `/` 返回 HTML |
| `TestWorkshopsCRUD` | list / create / get / rename / active 跟踪 / switch |
| `TestCloseAndDelete` | close 语义（deactivate 但列表里在）+ delete + keep_state 备份 |
| `TestUpload` | 上传真实现 + 自动命名 + 不覆盖用户已命名 + 404 |
| `TestSSE` | EventBus 等价验证 + 路由注册 |
| `TestMockEndpoints` | MOCK A/B/C/D 端点返回正确格式 |
| `TestErrorFormat` | 错误响应统一格式 |
| `TestStaticFiles` | `/static/js/app.js` 等可访问 |

**特殊**：最后一个 `test_sse_real_stream_smoke` 启动真 uvicorn + httpx 模拟浏览器连 SSE + emit 一次事件验证整条链路。默认会被运行，可手动 `-k "not _real_stream_smoke"` 排除（对网络端口敏感的话）。

### 运行

```bash
pytest tests/unit/test_http_server.py -v
# 27 passed in ~3s（除 SSE smoke ~10s）
```

---

## 七、设计决策记录

### 7.1 为什么 FastAPI 而不是 Flask

| 维度 | FastAPI | Flask |
|------|---------|------|
| 类型注解 + 自动生成 OpenAPI | ✅ | ❌ |
| Pydantic 校验请求体 | ✅ | 需手动 |
| BackgroundTasks（vs asyncio.create_task）| ✅ 内置 | ❌ |
| SSE StreamingResponse | ✅ 标准 | ❌ 需 hack |
| **学习曲线** | 较陡 | 平缓 |

**选 FastAPI 的理由**：自动 OpenAPI 文档（前端可省掉手写 API 文档的部分字段）；BackgroundTasks 让 mock 替换干净；StreamingResponse 让 SSE 一行代码搞定。

### 7.2 为什么用 `python -m src.ui` 而不是 `uvicorn src.ui.server:app`

```bash
# 推荐：
python -m src.ui --port 8000 --cache-root /data/...

# 也支持：
uvicorn src.ui.server:app --port 8000 --factory  # 需要 --factory 参数
```

后者也能跑（因为 `make_app` 是工厂函数），但 Python 入口方式：
- 加 argparse（--host, --port, --cache-root, --no-autosave, --reload）
- 单文件即可控生命周期（boot → uvicorn.run → 异常 exit handler）
- 复用现有 `Kernel` 而无需在 uvicorn 入口重复装配

### 7.3 为什么不用 BackgroundTasks.run_in_background 之类的 hack

避免：sync 端点 + `asyncio.create_task` 在 `TestClient` 同步上下文里会 raise `no running event loop`。`BackgroundTasks` 是 FastAPI 官方推荐方式，行为可预测。

---

## 八、文件清单

### 新建

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/ui/cli.py` | ~80 | CLI 入口（`python -m src.ui`） |
| `tests/unit/test_http_server.py` | ~250 | 27 个 HTTP 测试 |
| `docs/HTTP_API.md` | ~200 | 完整 API 文档（含 UI 契约） |

### 重写

| 文件 | 改动 |
|------|------|
| `src/ui/server.py` | 从 `app = FastAPI()` 改为 `make_app(kernel)` 工厂 |
| `src/ui/api/workshops.py` | 8 端点全部改用 `request.app.state.kernel` |
| `src/ui/api/events.py` | SSE 流用 FastAPI BackgroundTasks 风格 |
| `src/ui/api/analysis.py` | upload 真实现 + 4 mock 替换点显式标注 |
| `src/ui/static/js/app.js` | 重写主流程：欢迎页 + 关闭/删除/切换三套 |
| `src/ui/static/js/api.js` | 重写：错误统一 + close/delete 拆分 |

### 小改

| 文件 | 改动 |
|------|------|
| `src/ui/static/index.html` | 加欢迎面板 + × 关闭按钮 + "永久删除当前"按钮 |
| `src/ui/static/js/event_stream.js` | `connect()` 不再带 wid 参数 |
| `src/ui/api/analysis.py` | `_run_mock_*` 现在是 sync `add_task` 而非 `asyncio.create_task` |

---

## 九、UI ↔ Workshop 协作契约（写入 `docs/HTTP_API.md`）

写给 UI 团队严格遵守的约束：

1. **关闭/切换前 UI 必须先 disable 控件**：

   ```js
   state.busy = true;
   setControlsDisabled(true);
   await api.closeWorkshop(wid);
   ```

2. **inactive 车间前端不可写**：只有当前 active 接受修改；inactive 数据 disable。

3. **关闭 ≠ 删除**：close 保留数据在列表里（可再激活）；delete 永久清掉。

4. **SSE 不按 wid 过滤**：所有订阅者收到全部事件，前端按 `event.workshop_id` 过滤。

5. **错误统一格式**：`{"detail": {"error": "..."}}`（HTTP 非 2xx）。

---

## 十、已知限制与后续工作

### 已知限制

1. **CSS 没改**：`index.html` 加了 `<section id="welcome-panel">` 和 `<button class="btn-danger">`，但 `style.css` 里没新规则。功能能跑，UI 需要 CSS 团队补样式。
2. **mock 4 个替换点**：分离、分析、可视化、音频流都是 mock，接真插件时按 `[MOCK] A/B/C/D` 查找。
3. **测试 SSE smoke**：依赖真 uvicorn + 启动端口，在 CI 里若端口冲突可能 flaky。
4. **URL 下载未实现**：`handleUrlFetch` 是占位（提示"待实现"），需要 yt-dlp 集成。
5. **播放头为假动画**：`tick()` 用 RAF 推进，不播放真实音频。

### 后续开发路径

1. **CSS 美化**：补 `welcome-panel` / `btn-danger` / `workshop-close` / `busy-overlay` 等样式
2. **URL 下载**：集成 yt-dlp（已有 `src/audio/loader.py::load_audio_from_url`）
3. **真播放**：浏览器 `<audio src>` + AudioContext 同步播放头
4. **Plugin 接入**：按 `docs/HTTP_API.md` 4 个 `[MOCK]` 替换点对接真插件
5. **响应式**：UI 团队接 / 当前最小宽度 1024px
6. **CI**: 加 GitHub Actions 跑 `pytest tests/unit/test_*.py`

---

## 十一、与会议 / 文档的对应

| 文档 | 内容 |
|------|------|
| [docs/HTTP_API.md](../../HTTP_API.md) | 接口契约（本次新增） |
| [docs/meetings/2026-6-16-meeting.md](../../meetings/2026-6-16-meeting.md) §5 SSE 设计 | 会议定的事件总线 | 本次实现 |
| [p1-workshop-cache-system.md](p1-workshop-cache-system.md) §2/§6 | 车间 / 关闭语义 | 本次落地 |
| [p1-ui-layer.md](p1-ui-layer.md) | 旧版 mock UI 草稿 | 被本文**接替** |

---

## 十二、运行与验证

```bash
# 安装依赖
pip install fastapi "uvicorn[standard]" python-multipart httpx

# 启动服务（开发模式带 reload）
python -m src.ui --reload --port 8000

# 浏览器访问
open http://127.0.0.1:8000

# 测试
pytest tests/unit/test_http_server.py -v
# 27 passed in 9s

# 单测试 SSE smoke 跳过（CI 端口敏感时）
pytest tests/unit/test_http_server.py -v -k "not _real_stream_smoke"
```

---

> **下一步建议**：UI 团队 review `docs/HTTP_API.md` 确认端点可用，然后并行做：
>
> 1. 补 CSS（UI 团队）
> 2. 接 yt-dlp URL 下载（任何时候）
> 3. 接真 plugins（按 mock 替换点）
