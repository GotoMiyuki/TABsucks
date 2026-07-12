# Dev Log: UI Layer Rebuild — Hooking into Kernel + SSE Event Bus

> This is the **continuation** of [p1-ui-layer.md](p1-ui-layer.md). The previous
> mock-only UI has been replaced by a real implementation backed by
> `Kernel + EventBus + SSE`, plugging the Workshop system from
> [p1-workshop-cache-system.md](p1-workshop-cache-system.md).

**Date:** 2026-07-12
**Involved modules:**
1. [src/ui/server.py](../../src/ui/server.py) (rewritten) — `make_app(kernel)` factory
2. [src/ui/api/workshops.py](../../src/ui/api/workshops.py) (rewritten) — `request.app.state.kernel` pattern
3. [src/ui/api/events.py](../../src/ui/api/events.py) (rewritten) — SSE stream
4. [src/ui/api/analysis.py](../../src/ui/api/analysis.py) (rewritten) — upload **real** + remaining mock tagged A/B/C/D
5. [src/ui/cli.py](../../src/ui/cli.py) (**new**) — `python -m src.ui` entry point
6. [src/ui/static/index.html](../../src/ui/static/index.html) (modified) — welcome panel + × close button + permanent-delete button
7. [src/ui/static/js/app.js](../../src/ui/static/js/app.js) (rewritten) — close / delete / switch flows
8. [src/ui/static/js/api.js](../../src/ui/static/js/api.js) (rewritten) — error compat + SSE + close/delete split
9. [src/ui/static/js/event_stream.js](../../src/ui/static/js/event_stream.js) (minor) — `connect()` no longer takes `wid`
10. [tests/unit/test_http_server.py](../../tests/unit/test_http_server.py) (**new**) — 27 tests
11. [docs/HTTP_API.md](../../HTTP_API.md) (**new**) — API documentation

**Dependencies:** `fastapi`, `uvicorn[standard]`, `python-multipart`, `httpx` (test only)

---

## 1. Module Overview

The UI layer is the outermost layer of the TABsucks process. It exposes
[Kernel + Workshop + EventBus](../../src/kernel/kernel.py) to the browser via three
responsibilities:

* **HTTP REST endpoints**: workshop CRUD / upload / separation / analysis / visualization
* **SSE push**: forward every EventBus event to the browser in real time
* **Static assets**: single-page `index.html` + modular JS

### Core design principles

| Principle | Implementation |
|-----------|----------------|
| **Kernel factory injection**: `make_app(kernel)`, no module-level singletons | `app.state.kernel = kernel` |
| **Routes pull instance from app.state**: every request `request.app.state.kernel` | No global state |
| **SSE does not filter by `wid`**: client filters by `event.workshop_id` | EventBus stays simple |
| **Mock mode preserved**: 4 `[MOCK]` swap points clearly marked | Plug-in authors grep by `[MOCK] A/B/C/D` |
| **UI lock before API**: disable controls *before* calling close/switch | `setControlsDisabled(true)` → API call |

---

## 2. Architecture

### 2.1 Layering

```
Browser (HTML/JS)
    │
    ├── fetch  ─── HTTP ──→  src/ui/server.py::make_app()
    │                            │
    │                            ├─ /api/workshops/*        → workshops.py
    │                            ├─ /api/events (SSE)       → events.py
    │                            ├─ /api/*/upload/sep/...   → analysis.py (mock B/C/D)
    │                            │        ↑
    │                            │        └─ upload real impl
    │                            ▼
    │                        request.app.state.kernel
    │                            │
    │                            ├─ kernel.manager  ──→ MusicWorkshop
    │                            ├─ kernel.bus       ──→ SSE / business events
    │                            └─ kernel.manager.close() / switch() / ...
    │
    └── EventSource(name="/api/events") ←──── SSE data: {type, payload, workshop_id, emitted_at}
```

### 2.2 File layout

```
src/ui/
├── cli.py                    ← NEW: CLI entry (uvicorn config)
├── server.py                 ← REWRITTEN: make_app() factory
├── api/
│   ├── workshops.py          ← REWRITTEN: request.app.state.kernel
│   ├── events.py             ← REWRITTEN: SSE factory pattern
│   └── analysis.py           ← REWRITTEN: upload real + [MOCK] A/B/C/D
├── static/
│   ├── index.html            ← MODIFIED: welcome panel + × close
│   ├── css/style.css         ← untouched (UI team to add welcome/busy styles)
│   └── js/
│       ├── app.js            ← REWRITTEN: close/delete/switch flows
│       ├── api.js            ← REWRITTEN: error compat, close/delete
│       └── event_stream.js   ← MINOR: connect() no longer wid-scoped
└── mock_data/demo.json       ← untouched

tests/unit/test_http_server.py   ← NEW: 27 tests (+ real uvicorn SSE smoke)
docs/HTTP_API.md                 ← NEW: full API doc
```

### 2.3 Frontend ↔ Backend contract

| Frontend | Backend |
|----------|---------|
| Click × → `setBusy(true)` + `setControlsDisabled(true)` | `WorkshopManager.close()` → save + stop autosave; **MusicWorkshop stays in `_workshops`** |
| Click delete → confirm dialog | `WorkshopManager.delete()` → in-memory + disk removal (optional `keep_state` backup) |
| Click switch → `setBusy(true)` | `WorkshopManager.switch_to()` → close old + `resume_autosave` new |
| Upload file → FormData | `set_raw_audio_from_bytes` → `cache/raw_audio/` + auto-name (only when `WorkshopName == "New Workshop"`) |

**Key invariants**:
- Inactive workshops must be read-only from the UI (frontend disables controls)
- Close ≠ delete (close keeps the workshop in the sidebar; delete removes permanently)
- Sidebar always shows all workshops (active + inactive)

---

## 3. Core API endpoints

### 3.1 Workshop management (`/api/workshops*`)

| Method | Path | Behavior | Response |
|--------|------|----------|----------|
| GET | `/api/workshops` | List all (incl. closed / inactive) | 200 + `[{id, name, last_tab, active}]` |
| POST | `/api/workshops` | Create (deactivates old active) | 201 + `{id, name, last_tab}` |
| GET | `/api/workshops/{wid}` | Full state.json | 200 / 404 |
| PUT | `/api/workshops/{wid}` | Rename | 200 `{ok, name}` / 404 |
| POST | `/api/workshops/{wid}/close` | Close = deactivate | 200 `{ok, active_id}` / 404 |
| POST | `/api/workshops/{wid}/switch` | Switch active | 200 `{ok, active_id}` / 404 |
| GET | `/api/workshops-active` | Current active id (null = welcome page) | 200 `{active_id}` |
| DELETE | `/api/workshops/{wid}?keep_state={bool}` | Permanent delete | 200 / 404 |

### 3.2 Workflow (`/api/workshops/{wid}/*`)

| Method | Path | Behavior | Response | Status |
|--------|------|----------|----------|--------|
| POST | `/upload` | multipart upload | 200 `{ok, filename, name, rel_path}` | **real** |
| POST | `/separate` | Trigger separation | 200 `{ok, message}` | `[MOCK] A` |
| POST | `/analyze` | Trigger single-track analysis | 200 `{ok, message}` | `[MOCK] B` |
| GET | `/visualization?track=name` | Visualization JSON | 200 dict | `[MOCK] C` |
| GET | `/audio/{track}` | Return wav bytes | 200 audio/wav | `[MOCK] D` |

### 3.3 SSE (`/api/events`)

```
GET /api/events
  Response: text/event-stream
  Message body: {"type": "...", "payload": {...}, "workshop_id": "...", "emitted_at": float}
```

The server **does not filter by `workshop_id`**; the client filters on demand.

For full event list see [HTTP_API.md](../../HTTP_API.md#sse-events) or
`src/kernel/kernel.py::EventType`.

---

## 4. Key design decisions

### 4.1 Why `make_app(kernel)` factory instead of a module-level singleton

**Old version (deprecated)**:

```python
# src/ui/api/workshops.py  (old)
from src.kernel.core.workspace import WorkspaceManager
wm = WorkspaceManager()    # ← module-level, created at import time
wm.create("Demo Workshop") # ← violates "F5: don't activate on boot"
```

**Problems**:
1. Tests share global state across the same `pytest` process → cross-test pollution
2. Doesn't hook into the new `Kernel` (which carries more assembly logic)
3. "Demo Workshop" violates `meeting.md §4` ("boot in no-workshop state")

**New version**:

```python
# src/ui/server.py  (new)
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

**Benefits**:
- Tests: `TestClient(make_app(Kernel(tmp_path)))` → fully isolated
- Kernel assembly lives in CLI layer (top-level entry)
- HTTP server stays a thin protocol layer
- Future protocol swap (gRPC / WebSocket) won't touch business logic

### 4.2 Why SSE does not filter by `wid` server-side

**Options**:

| A. Broadcast everything (current) | B. Per-wid subscription |
|---|---|
| EventBus is one-to-many publish | EventBus maintains per-wid subscriber lists |
| Client filters by `event.workshop_id` | Server sends only relevant events |
| EventBus stays trivially simple | Higher transport efficiency |

**Why A**:
- MVP event volume is tiny (~5 progress events + 1 done per separation), bandwidth is negligible
- EventBus unit tests are simple (just check the Queue)
- Frontend keeps one persistent SSE connection, no "switch workshop → reconnect" complexity
- Migrate to option B later when traffic grows

Frontend filter pattern (already implemented in `event_stream.js`):

```javascript
const handlers = {
    separation_done: () => refreshStemGrid(),
    analysis_done: (p) => p.track === currentTrack ? showResult(p) : null,
    // Generic wrapper drops events for non-current workshops
};
```

### 4.3 Why `BackgroundTasks` instead of `asyncio.create_task`

**The bug**:

```python
# old analysis.py — broken
async def trigger():
    asyncio.create_task(_run_mock_separation(...))   # ← crashes in sync endpoint
```

Error: `RuntimeError: no running event loop` (no loop in sync FastAPI endpoint).

**Fix**: use FastAPI's built-in `BackgroundTasks` (runs after response is sent):

```python
@router.post("/separate")
def trigger(background: BackgroundTasks, ...):
    background.add_task(_run_mock_separation, wid, model, bus)
    return {"ok": True}
```

**Benefits**:
- Task runs after response (doesn't block API return)
- No need for an event loop to be present
- Works correctly with `TestClient` (background tasks run to completion)

### 4.4 Why close / delete are two separate APIs

**Discussion context** (meeting.md §4 + follow-up): close ≠ delete:
- **close = deactivate**: save data + stop background thread; MusicWorkshop instance *not released* (< 2KB each); still visible in sidebar; clicking the same item reuses the instance
- **delete = permanent**: in-memory + disk cleanup; optional state.json backup

**API design** (two endpoints, distinct semantics, distinct return values):

| Endpoint | Side effects | `active_id` impact |
|----------|--------------|---------------------|
| `POST /close` | No data deletion, MusicWorkshop retained | If `wid` is active → `null` |
| `DELETE /workshops/{wid}` | In-memory + disk removal | If `wid` is active → `null`; one fewer item in list |

Collapsing them into one endpoint with a query param would *break frontend semantics* and invite mistakes (forgetting the query = accidental delete).

### 4.5 Unified error response format

```http
HTTP 404 Not Found
{"detail": {"error": "workshop abc12345 does not exist"}}
```

We use `{"detail": {"error": msg}}` instead of FastAPI's default `{"detail": msg}` so the frontend can read the specific message directly:

```javascript
if (!r.ok) showToast(`Failed: ${r.error}`);   // r.error has the specific message
```

The `detail` outer wrapping is FastAPI-required; custom error codes live in `detail.error`.

### 4.6 Why `upload` is implemented as a real path (not mocked)

The old `upload_audio` did `ws.audio_path = file.filename` and never wrote to disk — meaning:
- Raw audio "disappeared" (in Python locals, gone on restart)
- Separator model can't access it later

The new version calls `set_raw_audio_from_bytes(data, filename)`:

```python
abs_path = ws.set_raw_audio_from_bytes(content, dst_name)
# → cache/raw_audio/<dst_name> written to disk
# → state.json gains RawAudioFilePath
# → auto-naming (only when WorkshopName == "New Workshop")
# → emit raw_audio_set event
# → save immediately (critical path)
```

This is fully equivalent to "download URL → local file → call set_raw_audio".

---

## 5. Function reference

### 5.1 `make_app(kernel)`

Constructs a FastAPI app and mounts 3 routers + static assets + root route.

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

### 5.2 SSE generator (`events.py`)

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
                    ev = q.get(timeout=0.3)   # 0.3s timeout lets disconnected check run
                except Empty:
                    continue
                yield f"data: {json.dumps(...)}\n\n"
        finally:
            kernel.bus.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream", ...)
```

Key points:
- **finally unsubscribe**: when the browser disconnects, `request.is_disconnected()` triggers, ensuring the Queue doesn't leak
- **0.3s poll instead of `await q.get()`**: the latter blocks forever on empty Queue, blocking the disconnected check
- **`X-Accel-Buffering: no`**: tells nginx not to buffer, push immediately to the client

### 5.3 Workshop auto-activation on POST `/api/workshops`

```python
def create_workshop(req, request):
    kernel = _kernel(request)
    info = kernel.create_workshop(name=req.name)
    # kernel.create_workshop internally:
    #   1. close current active (save + stop autosave)
    #   2. create new + immediately save empty state.json
    #   3. add to _workshops
    #   4. ws.resume_autosave()  ← start the new workshop's background thread
    #   5. active_id = new workshop id
    return info
```

Backend responsibility is entirely in Kernel. The frontend just does
`await api.createWorkshop(name)` then `refreshList()`.

### 5.4 UI lock flow (`app.js`)

```javascript
async function handleSwitchWorkshop(wid) {
    if (state.busy) return;
    state.busy = true;
    setControlsDisabled(true);         // Lock immediately
    document.body.classList.add('busy'); // CSS class to show spinner
    const r = await api.switchWorkshop(wid);
    if (!r.ok) {
        state.busy = false;
        setControlsDisabled(false);    // Roll back on failure
        document.body.classList.remove('busy');
        showToast(`Switch failed: ${r.error}`, 'error');
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

**Key**: the UI lock must happen *before* the API call; otherwise mid-switch edits could be silently lost.

---

## 6. Unit tests

`tests/unit/test_http_server.py` provides 27 tests:

| Class | What it tests |
|-------|---------------|
| `TestRoot` | `/` returns HTML |
| `TestWorkshopsCRUD` | list / create / get / rename / active tracking / switch |
| `TestCloseAndDelete` | Close semantics (deactivate but stays in list) + delete + keep_state backup |
| `TestUpload` | Upload real impl + auto-naming + don't overwrite user-named + 404 |
| `TestSSE` | EventBus equivalent verification + route registration |
| `TestMockEndpoints` | MOCK A/B/C/D endpoints return correct shape |
| `TestErrorFormat` | Unified error response format |
| `TestStaticFiles` | `/static/js/app.js` etc. are accessible |

**Special**: the last one, `test_sse_real_stream_smoke`, spins up a real uvicorn + uses httpx to simulate a browser connecting to SSE, then emits one event and verifies the whole chain. By default this runs; exclude with `-k "not _real_stream_smoke"` if your CI hates port contention.

### Run

```bash
pytest tests/unit/test_http_server.py -v
# 27 passed in ~3s (SSE smoke adds ~7s)
```

---

## 7. Decision log

### 7.1 Why FastAPI, not Flask

| Dimension | FastAPI | Flask |
|-----------|---------|-------|
| Type annotations + auto OpenAPI | ✅ | ❌ |
| Pydantic request validation | ✅ | manual |
| `BackgroundTasks` (vs `asyncio.create_task`) | built-in | ❌ |
| `StreamingResponse` for SSE | ✅ standard | ❌ hacky |
| **Learning curve** | steeper | gentler |

**Why FastAPI**: auto OpenAPI (less manual doc work); `BackgroundTasks` keeps mock swaps clean; `StreamingResponse` makes SSE one-liner.

### 7.2 Why `python -m src.ui` rather than `uvicorn src.ui.server:app`

```bash
# Recommended:
python -m src.ui --port 8000 --cache-root /data/...

# Also works (because make_app is a factory):
uvicorn src.ui.server:app --port 8000 --factory  # needs --factory
```

The Python entry form:
- Adds argparse (`--host`, `--port`, `--cache-root`, `--no-autosave`, `--reload`)
- Single file controls lifecycle (boot → uvicorn.run → graceful exit handler)
- Reuses the existing `Kernel` without re-assembly at the uvicorn entry point

### 7.3 Why not hack around `BackgroundTasks` with `asyncio.run` etc.

The problem was: sync endpoint + `asyncio.create_task` raises `no running event loop` under `TestClient`. `BackgroundTasks` is FastAPI's official, predictable way to run post-response tasks.

---

## 8. File manifest

### New

| File | Lines | Description |
|------|-------|-------------|
| `src/ui/cli.py` | ~80 | CLI entry (`python -m src.ui`) |
| `tests/unit/test_http_server.py` | ~250 | 27 HTTP tests |
| `docs/HTTP_API.md` | ~200 | Full API doc (incl. UI contract) |

### Rewritten

| File | Change |
|------|--------|
| `src/ui/server.py` | From `app = FastAPI()` to `make_app(kernel)` factory |
| `src/ui/api/workshops.py` | All 8 endpoints rewritten to use `request.app.state.kernel` |
| `src/ui/api/events.py` | SSE flow uses FastAPI's BackgroundTasks idiom |
| `src/ui/api/analysis.py` | Upload is real; 4 mock swap points explicitly tagged |
| `src/ui/static/js/app.js` | Rewritten main flow: welcome + close/delete/switch |
| `src/ui/static/js/api.js` | Rewritten: unified errors + close/delete split |

### Minor edits

| File | Change |
|------|--------|
| `src/ui/static/index.html` | Added welcome panel + × close button + "delete current" button |
| `src/ui/static/js/event_stream.js` | `connect()` no longer takes `wid` parameter |
| `src/ui/api/analysis.py` | `_run_mock_*` is now sync `add_task` instead of `asyncio.create_task` |

---

## 9. UI ↔ Workshop contract (recorded in `docs/HTTP_API.md`)

Constraints for the UI team:

1. **Disable UI controls before calling close/switch**:
   ```js
   state.busy = true;
   setControlsDisabled(true);
   await api.closeWorkshop(wid);
   ```

2. **Inactive workshops are read-only from the UI**: only the current active accepts modifications; inactive data should be disabled.

3. **Close ≠ delete**: close keeps the data in the list (re-activatable); delete removes permanently.

4. **SSE does not filter by `wid`**: all subscribers receive all events; frontend filters by `event.workshop_id`.

5. **Unified error format**: `{"detail": {"error": "..."}}` (HTTP non-2xx).

---

## 10. Known limitations & next steps

### Known limitations

1. **CSS not updated**: `index.html` added `<section id="welcome-panel">` and `<button class="btn-danger">`, but `style.css` has no new rules. Functions work; UI team needs to add styles.
2. **4 mock swap points**: separation, analysis, visualization, audio stream are mocked. Plug-in authors grep `[MOCK] A/B/C/D]`.
3. **SSE smoke test** depends on real uvicorn + a port — may flake in CI with port contention.
4. **URL download not implemented**: `handleUrlFetch` is a placeholder ("待实现 / to be implemented"); needs yt-dlp integration.
5. **Playhead is fake animation**: `tick()` uses RAF, doesn't play real audio.

### Next development path

1. **CSS polish**: add `welcome-panel` / `btn-danger` / `workshop-close` / `busy-overlay` styles
2. **URL download**: integrate yt-dlp (already exists in `src/audio/loader.py::load_audio_from_url`)
3. **Real playback**: browser `<audio src>` + AudioContext sync playhead
4. **Plug-in integration**: replace 4 `[MOCK]` swap points with real implementations
5. **Responsive layout**: UI team / current min-width 1024px
6. **CI**: add GitHub Actions running `pytest tests/unit/test_*.py`

---

## 11. Cross-references

| Doc | Content |
|-----|---------|
| [docs/HTTP_API.md](../../HTTP_API.md) | API contract (added in this iteration) |
| [docs/meetings/2026-6-16-meeting.md](../../meetings/2026-6-16-meeting.md) §5 SSE design | Meeting-mandated event bus | Implemented here |
| [p1-workshop-cache-system.md](p1-workshop-cache-system.md) §2/§6 | Workshop / close semantics | Implemented here |
| [p1-ui-layer.md](p1-ui-layer.md) | Old mock-UI draft | **Superseded by this doc** |

---

## 12. Run & verify

```bash
# Install deps
pip install fastapi "uvicorn[standard]" python-multipart httpx

# Run dev server (with reload)
python -m src.ui --reload --port 8000

# Open
open http://127.0.0.1:8000

# Tests
pytest tests/unit/test_http_server.py -v
# 27 passed in 9s

# Skip SSE smoke if port-sensitive
pytest tests/unit/test_http_server.py -v -k "not _real_stream_smoke"
```

---

> **Next steps**: UI team reviews `docs/HTTP_API.md` to confirm endpoints are usable; in parallel:
>
> 1. Add CSS (UI team)
> 2. Hook up yt-dlp URL download (anytime)
> 3. Hook up real plugins (per mock swap points)
