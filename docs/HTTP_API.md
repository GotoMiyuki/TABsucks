# HTTP API 文档

> 给 UI 团队对接使用的接口说明。Base URL: `http://127.0.0.1:8000`。
> 后端：`src/kernel/kernel.py::Kernel` + `src/ui/server.py::make_app`。
>
> 业务响应通常为 JSON；音频/MIDI 返回文件，SSE 返回事件流。业务错误通常为 `{"detail": {"error": "<msg>"}}`，框架参数校验使用 FastAPI 的 422 格式。
>
> 当前前端为静态 HTML/JavaScript，通过 FastAPI HTTP API 与 SSE 事件流通信。分离默认插件为
> `separation_bs_roformer`；`BS-RoFormer` 等旧显示名称也映射到该真实插件。`example_separator`
> 仅用于明确选择的开发示例。manifest 可发现不代表本机依赖、权重和硬件已经就绪；执行失败会发出
> `separation_failed` 事件并携带原因。

---

## 通用约定

| 项 | 约定 |
|----|------|
| Content-Type | `application/json`（上传用 `multipart/form-data`） |
| 字符集 | UTF-8 |
| 时间 | 浮点秒 |
| 路径字段 | 相对路径（以 `workshop_<id>/` 为基准） |
| SSE 事件格式 | 见 `/api/events` |
| 错误 | 4xx/5xx + body `{"detail": {"error": "..."}}` |
| 后端 base | Kernel 实例挂在 `app.state.kernel`（FastAPI 工厂） |

---

## 车间管理

### GET `/api/workshops`

列出所有车间（**包含**已关闭、在磁盘上但未活跃的）。

**响应 200**

```json
[
  {"id": "abc12345", "name": "MySong", "last_tab": "Tab1", "active": true},
  {"id": "def67890", "name": "Old",    "last_tab": "Tab3", "active": false}
]
```

### POST `/api/workshops`

创建新车间。会自动 deactivate 任何当前 active 车间。

**Body**

```json
{"name": "MySong"}      // 可选，默认 "New Workshop"
```

**响应 201**

```json
{"id": "abc12345", "name": "MySong", "last_tab": "Tab1"}
```

### GET `/api/workshops/{wid}`

车间完整 state.json（dict 形式）。

根字段 `SchemaVersion` 当前为 1；旧文件启动时迁移，未来版本拒绝加载并保留原文件。详细规则见 [状态版本说明](implementation_status.md#状态版本与恢复规则)。

**响应 200**

```json
{
  "WorkshopName": "MySong",
  "SchemaVersion": 1,
  "LastTab": "Tab1",
  "TabState": {
    "Tab1": {"RawAudioFilePath": "raw_audio/song.mp3"},
    "Tab2": {
      "SeparationState": "done",
      "TrackAudioFilePath": {"vocals": "...", "guitar": "..."},
      "SelectedTracks": ["vocals", "guitar"]
    },
    "Tab3": {},
    "Tab4": {}
  }
}
```

**响应 404**: `{"detail": {"error": "车间 abc12345 不存在"}}`

### PUT `/api/workshops/{wid}`

重命名车间。

**Body**: `{"name": "NewName"}`

**响应 200**: `{"ok": true, "name": "NewName"}`

### PUT `/api/workshops/{wid}/selected-tracks`

保存 Tab2 多选音轨。仅当前 active 车间可写，且必须已经完成分离；每个音轨必须存在于
`TrackAudioFilePath`。

**Body**

```json
{"tracks": ["bass", "piano", "guitar"]}
```

**响应 200**

```json
{"ok": true, "tracks": ["bass", "piano", "guitar"]}
```

**响应 409**：车间未激活、分离未完成，或请求包含当前不可用的音轨。

### PUT `/api/workshops/{wid}/current-tab`

立即保存当前 UI Tab，供关闭或重启后恢复。仅当前 active 车间可写。

**Body**: `{"tab": "Tab3"}`

**响应 200**: `{"ok": true, "tab": "Tab3"}`

**响应 409**：车间未激活。

### DELETE `/api/workshops/{wid}?keep_state={true|false}`

**永久删除**车间（内存 + 磁盘）。`keep_state=true` 时 state.json 备份到 `recycle_bin/<id>_state.json.bak`。

**响应 200**: `{"ok": true}`

若车间仍有任务，返回 **202**：`{"ok": true, "operation_id": "...", "status": "running"}`。此时尚未删除；轮询 `GET /api/tasks/{operation_id}` 至 `done` 后才算完成。关闭接口同理。提交中的任务不可取消，关闭会等待提交结束。

**响应 404**: 不存在或已删除。

### POST `/api/workshops/{wid}/close`

**关闭**车间 = deactivate。
- MusicWorkshop 实例仍留内存（每个 < 2KB，可忽略）
- 磁盘数据保留
- 列表里仍可见（与 DELETE 不同！）
- 若 `wid` 是当前 active，则 `active_id` 变 None → 欢迎页

**响应 200**: `{"ok": true, "active_id": null | "..."}`

有在途任务时返回 **202** 和 `operation_id`，任务取消/排空后才释放运行时。相同车间关闭或删除期间拒绝新任务。

### POST `/api/workshops/{wid}/switch`

切换 active。**等价于** 关闭旧车间（save + stop autosave） + 激活新车间（resume autosave）。

**响应 200**: `{"ok": true, "active_id": "abc12345"}`

### GET `/api/workshops-active`

当前 active 车间 ID（无则 null）。前端用它判断"是否在欢迎页"。

**响应 200**: `{"active_id": "abc12345"} | {"active_id": null}`

---

## 上传与流程

### POST `/api/workshops/{wid}/upload`

上传原始音频（multipart）。

**字段**: `file: <binary>`（任意音频格式）

**响应 200**

```json
{
  "ok": true,
  "filename": "<task_id>_song.mp3", // 防止覆盖旧输入
  "name": "song",                  // 自动命名（仅当原 name = "New Workshop"）
  "rel_path": "raw_audio/<task_id>_song.mp3" // state.json 里的相对路径
}
```

**自动命名规则**：仅当 `WorkshopName == "New Workshop"` 时，用 `Path(filename).stem` 重命名。

上传按 1 MiB 分块写临时文件，默认上限 512 MiB（`TABSUCKS_MAX_UPLOAD_BYTES`）。URL 导入在工作线程下载和复制；两者与分离/分析在同一车间互斥。冲突返回 409。音频解码前另按 `TABSUCKS_AUDIO_MEMORY_BUDGET_MB` 检查估算内存，默认 1024 MiB。

---

### POST `/api/workshops/{wid}/separate`

通过 Kernel/Orchestrator 启动真实分离插件任务，请求本身不等待推理结束。

**Body**: `{"model": "separation_bs_roformer"}`

**响应 200**: `{"ok": true, "task": "separation_bs_roformer", "task_id": "...", "status": "running"}`；相同在途请求返回同一 `task_id`，冲突请求返回 409。

**触发事件（通过 `/api/events` 接收）**：
- `separation_started`
- `separation_progress` × N（当前阶段的真实进度 0~1，无法测量时为 null）
- `separation_done`（最终 Workshop payload: `{tracks: ["vocals", ...]}`）

---

### POST `/api/workshops/{wid}/analyze`

启动单个 track 的真实分析插件任务。插件必须与音轨兼容；Tab3 按插件 manifest 的
`input_stems` 过滤候选模型。

**Body**: `{"track": "guitar", "plugin": "chord_ismir2019"}`

**响应 200**: `{"ok": true, "task": "chord_ismir2019", "task_id": "...", "status": "running"}`；相同在途请求复用任务，冲突请求返回 409。

**触发事件**：`analysis_started` → `analysis_progress` → `analysis_done`。
完成事件包含 `track`、`plugin`、`task_id`、`result_path` 和规范化后的 `result`。

### GET `/api/tasks/{task_id}` · GET `/api/workshops/{wid}/tasks` · POST `/api/tasks/{task_id}/cancel`

任务查询返回 `task_id`、`workshop_id`、`kind`、`plugin`、`track`、`status`、`stage`、`error`、可空的 `progress`、`progress_detail` 与时间戳；车间列表返回同结构数组。状态为 `queued/running/committing/cancelling/done/failed/cancelled/interrupted`。取消接口返回当前任务结构；在 `committing` 阶段或关闭/删除操作上请求取消返回 409。同步模型不能被强杀，`cancelling` 可能持续至模型线程退出。任务记录只在当前服务进程内可查询，重启后业务状态中的在途任务显示为 `interrupted`。

### GET `/api/workshops/{wid}/analysis-results`

读取每条音轨最新的已完成分析结果，并返回产生该结果的插件名。Tab3 使用
`result_plugins` 判断持久化结果是否与当前下拉框选择一致。

**响应 200**

```json
{
  "ok": true,
  "results": {
    "guitar": {"chords": [{"start": 0.0, "end": 1.0, "chord": "C"}]}
  },
  "result_plugins": {
    "guitar": "chord_ismir2019"
  }
}
```

---

### GET `/api/workshops/{wid}/visualization?track={name}&result_id={id}`

获取 Tab4 可视化 JSON。`track=full` 时波形来自原始音频；指定音轨时波形来自对应的
分离 stem。节拍与和弦读取该音轨最新完成的 Tab3 分析结果；没有结果时 `beats`、`chords` 返回空数组。
音频缺失或解码失败时，返回空 `peaks`、零时长以及 `metadata.hasAudioData=false`，不生成随机波形。
`metadata.hasBeatData`、`hasChordData` 表示是否取得相应分析数据。节拍位置目前由节奏结果中的 BPM 推算，并非逐拍检测输出。

- 合法 `track` 为 `full`、`vocals`、`drums`、`bass`、`piano`、`guitar`、`other`；非法名称返回 400。
- `metadata.sourceVersion` 为当前音频资源的不透明版本标识，无文件时为 `null`。
- `metadata.result` 为 `{id, plugin, taskId}`，无有效和弦结果时为 `null`。
- `metadata.availableResults` 列出当前音轨的有效历史和弦结果，按新到旧排列，字段同 `result`；供前端选择显示版本。
- 分轨可选 `result_id`，用于读取明确指定的成功结果；不传时读取最新有效和弦结果。
- 不匹配、文件已更新或失效的结果标识返回 409；不能提交客户端文件路径。`full` 不接受分轨结果标识。
- 新前端导出时提交当前视图的 `result.id`，避免多个插件或重跑后显示与导出不一致。
- 波形服务仅缓存压缩峰值，按文件路径、大小和修改时间失效，不缓存完整解码音频。

**响应 200**

```json
{
  "waveform": {"peaks": [...], "duration": 30.0, "sampleRate": 44100, "frameInterval": 0.015, "totalFrames": 2000},
  "beats": [{"time": 0.5, "measure": 1, ...}],
  "chords": [{"name": "C:maj", "start": 0.0, "end": 2.5, ...}],
  "metadata": {"duration": 30.0, ...}
}
```

---

### GET / HEAD `/api/workshops/{wid}/audio/{track}?download=true&v={sourceVersion}`

获取真实音频文件。`full` 读取原曲，其他合法名称只读取对应分轨。缺失分轨返回 404，
不会回退到原曲；非法轨名返回 400。支持 HTTP Range 请求，供浏览器播放器定位。

可选 `download=true` 返回附件响应，文件名为清理非法字符后的“项目名_音轨名.实际扩展名”；
默认返回行内响应。媒体类型根据实际文件格式生成，原曲不强行标记为 WAV。
可选 `v` 校验可视化响应中的 `sourceVersion`，版本变化返回 409。`HEAD` 使用相同校验和响应头、无文件正文。
前端先检查 `HEAD`，再通过文件链接发起单轨下载，不把完整音频加载为 Blob。

**响应 200 / 206**: 实际格式对应的媒体类型，例如 `audio/wav` 或 `audio/mpeg`。

---

### GET `/api/workshops/{wid}/midi?tracks={name}&tracks={name}`

把 Tab2 当前已选音轨的最新和弦分析区间导出为标准多轨 MIDI。每个请求音轨对应一个
MIDI instrument track，和弦区间会转换为同时起止的组成音。该接口导出的是和弦分析
结果，不是旋律音高转录。

- `tracks` 可重复传递；不传时默认导出当前全部 `SelectedTracks`。
- 请求音轨必须属于当前 `SelectedTracks`。
- 每条请求音轨必须存在最新的已完成和弦分析结果。
- 可重复传递 `result_ids`，与显式 `tracks` 按请求顺序一一对应；数量不符或音轨重复返回 400。
- 提供标识时导出明确的对应结果，并校验项目、音轨、结果文件和当前音源版本；失效返回 409。
- 相关分离或该音轨分析正在进行时返回 409；不静默跳过缺失结果。
- 旧请求未提供 `result_ids` 时保留默认选择规则。

例：`/api/workshops/{wid}/midi?tracks=guitar&result_ids={visualization.metadata.result.id}`。

**响应 200**: `audio/midi`，附件文件名
`tabsucks_<workshop_id>_selected.mid`。

**响应 409**: 没有已选音轨、包含未选音轨，或任一音轨缺少有效和弦结果。

---

## SSE 事件流

### GET `/api/events`

**订阅全局事件流**。所有 kernel.bus 的事件都会推到这条 SSE 连接上。

**重要**：SSE 端点**不**按 workshop_id 过滤；前端按 `ev.workshop_id` 自行决定是否处理。

**SSE message 格式**

```
data: {"type": "<event>", "payload": {...}, "workshop_id": "<wid>", "emitted_at": 1783...}
\n\n
```

**事件类型**（与 `src/kernel/kernel.py::EventType` 一致）：

| Event | Payload | 何时 |
|-------|---------|------|
| `workshop_created` | `{workshop_id, name}` | 新车间 |
| `workshop_closed` | `{workshop_id}` | 关闭 |
| `workshop_deleted` | `{workshop_id}` | 永久删除 |
| `workshop_switched` | `{workshop_id}` | 切换 |
| `workshop_load_failed` | `{workshop_id, error}` | 启动时坏车间 |
| `raw_audio_set` | `{path}` | 上传原音频 |
| `separation_started` | `{model}` | 分离开始 |
| `separation_progress` | `{task_id, plugin, stage, progress, completed?, total?, unit?, detail?, device?, updated_at}` | 当前阶段的真实进度 |
| `separation_done` | `{tracks: [...]}` | Workshop 持久化分轨后完成 |
| `separation_failed` | `{model, error}` | 分离失败 |
| `analysis_started` | `{track, plugin, task_id?}` | 分析开始 |
| `analysis_done` | `{track, plugin, task_id?, result_path}` | 分析完成 |
| `analysis_failed` | `{track, plugin, task_id?, error}` | 分析失败 |
| `mix_state_changed` | `{track, volume, mute, solo}` | Tab4 混音改 |
| `playback_state` | `{current_time, is_playing, speed, loop}` | 播放头动 |
| `state_saved` | `{reason: "autosave"}` | autosave flush |

分离进度的 `progress` 是**当前阶段**的 0～1 完成比例，无法测量时为 `null`；
不根据运行时间估算，也不表示整体任务的完成比例。切换阶段时重新计数。
`completed`/`total` 是真实计数，`unit` 为 `bytes`（模型下载）、
`chunks`（BS-RoFormer 已完成推理片段）或 `stems`（生成、校验、保存的分轨）。
下载长度未知时仅报告已下载字节数。未识别的推理布局仅报告阶段。

阶段依次包含 `loading_audio`、`loading_plugin`、`preparing_model`、
`downloading_model`（有需要时）、`loading_model`、`preparing_audio`、
`separating_audio`、`writing_stems`、`reading_stems`、`saving_results`；
GPU 任务还可能包含 `waiting_for_gpu`、`preparing_vram`。
单一阶段到 100% 后仍需完成后续保存，只有 `separation_done` 表示任务成功。

`GET /api/tasks/{task_id}` 和 `GET /api/workshops/{wid}/tasks` 同时返回
`stage`、可空 `progress`、`progress_detail` 和 `updated_at`，
其中 `progress_detail` 保留计数、单位、文件/音轨名和实际设备。
刷新或重连后可以用快照恢复页面提示；使用 `task_id` 和 `updated_at` 丢弃旧事件。
取消请求后保持 `cancelling`，直到模型退出；失败或取消不显示完成百分比。

---

## UI ↔ Workshop 协作约束（核心契约）

按 [src/kernel/core/workshop.py](src/kernel/core/workshop.py) 中的 docstring：

1. **关闭/切换前 UI 必须先 disable 控件**：
   ```js
   // app.js
   state.busy = true;
   setControlsDisabled(true);   // UI 立刻锁
   await api.closeWorkshop(wid); // 然后才发请求
   ```
2. **inactive 车间不应可写**：只有当前 active 车间接受用户修改；inactive 数据在 UI 上 disable
3. **关闭 ≠ 删除**：关闭保留数据在列表里（可再激活）；删除永久清掉

---

## 错误响应格式

```http
HTTP 404 Not Found
{"detail": {"error": "车间 abc 不存在"}}
```

```http
HTTP 503 Service Unavailable  
{"detail": {"error": "Kernel 未 boot"}}
```

前端检测：

```js
if (!r.ok) showToast(`失败: ${r.error}`, 'error');
```

---

## 启动方式

```bash
# 默认配置（http://127.0.0.1:8000，cache 在 ./cache）
python -m src.ui

# 自定义
python -m src.ui --host 0.0.0.0 --port 9000 --cache-root /data/tabsucks

# 开发模式（代码改动自动 reload）
python -m src.ui --reload
```

---

## 与 WorkshopState.json 字段对照（参考）

详见 `src/kernel/core/workshop.py::WorkshopState`，完整字段：

| state.json 字段 | Python 字段 | 类型 |
|----------------|-------------|------|
| `WorkshopName` | `workshop_name` | str |
| `LastTab` | `last_tab` | "Tab1"\|"Tab2"\|"Tab3"\|"Tab4" |
| `TabState.Tab1.RawAudioFilePath` | `tab_state.tab1.raw_audio_file_path` | str\|null |
| `TabState.Tab2.SeparationState` | `tab_state.tab2.separation_state` | "not_started"\|"running"\|"done"\|"failed" |
| `TabState.Tab2.SeparationModelName` | `tab_state.tab2.separation_model_name` | str\|null |
| `TabState.Tab2.SeparationModelPath` | `tab_state.tab2.separation_model_path` | str\|null |
| `TabState.Tab2.TrackAudioFilePath` | `tab_state.tab2.track_audio_file_path` | `{name: rel_path}` |
| `TabState.Tab2.SelectedTracks` | `tab_state.tab2.selected_tracks` | `list[track_name]` |
| `TabState.Tab3[f"{track}::{task_id}"].AnalysisToolName` | `tab_state.tab3[k].analysis_tool_name` | str\|null |
| `TabState.Tab3[...].AnalysisState` | `tab_state.tab3[k].analysis_state` | RunState |
| `TabState.Tab3[...].AnalysisResultPath` | `tab_state.tab3[k].analysis_result_path` | str\|null |
| `TabState.Tab3[...].AnalysisTaskId` | `tab_state.tab3[k].analysis_task_id` | str\|null |
| `TabState.Tab4[track].SelectedAnalysisResultPath` | `tab_state.tab4[k].selected_analysis_result_path` | str\|null |
| `TabState.Tab4[track].MixState` | `tab_state.tab4[k].mix_state` | MixState |

