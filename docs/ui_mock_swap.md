# UI Mock → 真实后端替换清单

演示阶段所有后端操作走 Mock。以下是替换为真实实现时需要改动的**精确位置**。

---

## 后端替换（Python）

### 替换点 A：分离

**文件**: `src/ui/api/analysis.py`
**函数**: `_run_mock_separation(wid)`

```python
# 当前（mock）:
async def _run_mock_separation(wid):
    bus.emit(WorkshopEvent(wid, "separation_started", ...))
    for i in range(101):
        await asyncio.sleep(0.03)
        bus.emit(...)
    bus.emit(WorkshopEvent(wid, "separation_done", ...))

# 替换为:
async def _run_real_separation(wid):
    ws = wm._workspaces[wid]
    from src.plugins.separation.separator import Separator
    from src.audio.loader import load_audio
    audio = load_audio(ws.audio_path)
    bus.emit(WorkshopEvent(wid, "separation_started", {"model": "BS-RoFormer"}))
    sep = Separator()
    result = await asyncio.to_thread(sep.separate, audio)
    ws._separation_result = result
    bus.emit(WorkshopEvent(wid, "separation_done", {"stems": ["vocals","drums","bass","piano","guitar","other"]}))
```

同时修改 `trigger_separation()` 中的 `asyncio.create_task(_run_mock_separation(wid))` → `asyncio.create_task(_run_real_separation(wid))`

---

### 替换点 B：逐轨分析

**文件**: `src/ui/api/analysis.py`
**函数**: `_run_mock_analysis(wid, track)`

```python
# 替换为:
async def _run_real_analysis(wid, track):
    ws = wm._workspaces[wid]
    from src.kernel.core.analysis_engine import AnalysisEngine
    from src.kernel.core.resource_controller import ResourceController
    from src.kernel.core.plugin_manager import PluginManager

    rc = ResourceController()
    # 将分离后的音轨放入 RC
    if ws._separation_result:
        rc.set_buffer(track, ws._separation_result.get_track(track))
    pm = PluginManager(rc)
    # 注册需要的插件...
    engine = AnalysisEngine(rc, pm)
    result = await asyncio.to_thread(engine.run_single, track, "chord_chordnet_2e1d")
    bus.emit(WorkshopEvent(wid, "analysis_done", {"track": track, "result": result}))
```

---

### 替换点 C：可视化数据

**文件**: `src/ui/api/analysis.py`
**函数**: `get_visualization(wid, track)`

```python
# 当前返回 mock_data/demo.json 或 _mock_waveform()
# 替换为:
from src.visualizer.export import export_visualization_json

@router.get("/workshops/{wid}/visualization")
async def get_visualization(wid, str, track: str = "full"):
    ws = wm._workspaces[wid]
    # 构造 AudioData 或直接用 analysis result
    viz = export_visualization_json(audio=..., beat_info=..., chord_events=...)
    return viz
```

---

### 替换点 D：音轨音频流

**文件**: `src/ui/api/analysis.py`
**函数**: `get_audio(wid, track)`

```python
# 当前生成测试正弦波 WAV
# 替换为：将 ws._separation_result 中的 numpy 数组转为 WAV 返回
import soundfile as sf
import io

@router.get("/workshops/{wid}/audio/{track}")
async def get_audio(wid: str, track: str):
    ws = wm._workspaces[wid]
    samples = ws._separation_result.get_track(TrackId(track))
    buf = io.BytesIO()
    sf.write(buf, samples, ws._separation_result.sample_rate, format='WAV')
    buf.seek(0)
    return StreamingResponse(buf, media_type="audio/wav")
```

---

## 前端替换（JS）

前端**不需要大改**。所有 API 调用集中在 `src/ui/static/js/api.js`，返回格式不变。

唯一需要注意：
- 如果后端 API 路径变了，只需改 `api.js` 中的 URL
- 如果后端返回的 JSON 字段名变了，修改 `app.js` 中对应的取值逻辑

---

## 需要安装的真实依赖

替换 Mock 后需要在环境中安装（已在 `requirements.txt`）：

```bash
pip install -r requirements.txt   # torch, audio-separator, librosa, soundfile 等
```

---

## 文件清单

| 文件 | 演示时 | 替换后 |
|------|--------|--------|
| `src/ui/api/analysis.py` | Mock 分离/分析/波形/音频 | 接入真实 Separator、AnalysisEngine、visualizer |
| `src/ui/api/workshops.py` | 已用真实 WorkspaceManager | **无需改动** |
| `src/ui/api/events.py` | 真正的 SSE 推送 | **无需改动** |
| `src/kernel/core/event_bus.py` | 真正的事件总线 | **无需改动** |
| `src/ui/server.py` | 真正的 FastAPI 入口 | **无需改动** |
| `src/ui/static/js/api.js` | 同源 fetch | 仅当 API 路径变化时改 URL |
| `src/ui/static/js/app.js` | 纯前端逻辑 | 仅当 JSON 字段变化时微调 |
| `src/ui/static/js/waveform.js` | Canvas 渲染 | **无需改动** |
| `src/ui/static/js/timeline.js` | 时间轴控制 | **无需改动** |
| `src/ui/static/js/mixer.js` | 混音台 UI | **无需改动** |
| `src/ui/static/js/event_stream.js` | SSE 封装 | **无需改动** |
| `src/ui/mock_data/demo.json` | 预置数据 | **删除即可** |
