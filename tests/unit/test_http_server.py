"""HTTP server (UI API) 端到端测试。

用 :py:class:`fastapi.testclient.TestClient` 替代真正 uvicorn，启动 < 1 秒。
所有测试用一个 Fresh Kernel（tmp_path cache），互不污染。
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.kernel.kernel import Kernel
from src.ui.api.events import events
from src.ui.server import make_app

# 别名，兼容旧测试中的 ``_P`` 局部引用
_P = Path


@pytest.fixture
def kernel_and_client(tmp_path: Path):
    """构造临时 cache + 已 boot 的 Kernel + TestClient。"""
    kernel = Kernel(cache_root=tmp_path, autosave=False)
    kernel.boot()
    client = TestClient(make_app(kernel))
    return kernel, client


class TestRoot:
    def test_index_returns_html(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()


class TestWorkshopsCRUD:
    def test_list_empty(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.get("/api/workshops")
        assert r.status_code == 200
        assert r.json() == []

    def test_create(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.post("/api/workshops", json={"name": "MySong"})
        assert r.status_code == 201
        body = r.json()
        assert "id" in body
        assert body["name"] == "MySong"

    def test_create_default_name(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.post("/api/workshops", json={})
        assert r.status_code == 201
        assert r.json()["name"] == "New Workshop"

    def test_get_state(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        wid = client.post("/api/workshops", json={"name": "X"}).json()["id"]
        r = client.get(f"/api/workshops/{wid}")
        assert r.status_code == 200
        state = r.json()
        assert state["WorkshopName"] == "X"

    def test_get_state_missing(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.get("/api/workshops/nonexistent")
        assert r.status_code == 404
        assert "error" in r.json()["detail"]

    def test_rename(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        wid = client.post("/api/workshops", json={"name": "Old"}).json()["id"]
        r = client.put(f"/api/workshops/{wid}", json={"name": "New"})
        assert r.status_code == 200
        assert client.get(f"/api/workshops/{wid}").json()["WorkshopName"] == "New"

    def test_rename_missing_returns_404(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.put("/api/workshops/nonexistent", json={"name": "X"})
        assert r.status_code == 404

    def test_active_tracking(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        client.post("/api/workshops", json={"name": "A"})  # noqa: F841
        b = client.post("/api/workshops", json={"name": "B"}).json()["id"]
        # 列表里 active 标志正确
        list_r = client.get("/api/workshops").json()
        active_ids = [w["id"] for w in list_r if w["active"]]
        assert active_ids == [b]  # 最新创建的是 active
        # /workshops-active
        ar = client.get("/api/workshops-active").json()
        assert ar == {"active_id": b}

    def test_switch(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        a = client.post("/api/workshops", json={"name": "A"}).json()["id"]
        b = client.post("/api/workshops", json={"name": "B"}).json()["id"]
        # 现在 active 是 B
        r = client.post(f"/api/workshops/{a}/switch")
        assert r.status_code == 200
        assert client.get("/api/workshops-active").json() == {"active_id": a}
        # 切回 B
        client.post(f"/api/workshops/{b}/switch")

    def test_switch_missing_404(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.post("/api/workshops/nope/switch")
        assert r.status_code == 404


class TestCloseAndDelete:
    def test_close_deactivates_keeps_in_list(
        self, kernel_and_client
    ) -> None:
        _, client = kernel_and_client
        wid = client.post("/api/workshops", json={"name": "X"}).json()["id"]
        # close
        r = client.post(f"/api/workshops/{wid}/close")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["active_id"] is None
        # 列表里仍在
        list_r = client.get("/api/workshops").json()
        assert any(w["id"] == wid for w in list_r)
        assert all(not w["active"] for w in list_r)
        # 再激活
        client.post(f"/api/workshops/{wid}/switch")
        assert client.get("/api/workshops-active").json() == {"active_id": wid}

    def test_close_missing(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.post("/api/workshops/nope/close")
        assert r.status_code == 404

    def test_delete_keeps_state(self, kernel_and_client, tmp_path) -> None:
        kernel, client = kernel_and_client
        wid = client.post("/api/workshops", json={"name": "X"}).json()["id"]
        r = client.delete(f"/api/workshops/{wid}?keep_state=true")
        assert r.status_code == 200
        # 列表里删除
        assert all(w["id"] != wid for w in client.get("/api/workshops").json())
        # 备份在
        bak = tmp_path / "recycle_bin" / f"{wid}_state.json.bak"
        assert bak.exists()

    def test_delete_no_keep(self, kernel_and_client, tmp_path) -> None:
        kernel, client = kernel_and_client
        wid = client.post("/api/workshops", json={"name": "X"}).json()["id"]
        client.delete(f"/api/workshops/{wid}")
        # 备份不在
        bak = tmp_path / "recycle_bin" / f"{wid}_state.json.bak"
        assert not bak.exists()


class TestUpload:
    def test_upload_auto_renames(self, kernel_and_client, tmp_path) -> None:
        """上传时自动改名（仅当 name 仍是默认 "New Workshop"）。"""
        kernel, client = kernel_and_client
        # 默认名
        wid = client.post("/api/workshops", json={}).json()["id"]
        assert wid
        payload = b"\xff\xfb\x90\x44" + b"\x00" * 1000
        files = {"file": ("my_song.mp3", io.BytesIO(payload), "audio/mpeg")}
        r = client.post(f"/api/workshops/{wid}/upload", files=files)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["name"] == "my_song"  # Path("my_song.mp3").stem
        # 文件落盘
        assert (tmp_path / f"workshop_{wid}" / "raw_audio" / "my_song.mp3").exists()

    def test_upload_does_not_overwrite_user_name(
        self, kernel_and_client, tmp_path
    ) -> None:
        """已重命名的车间，上传不会改名。"""
        kernel, client = kernel_and_client
        wid = client.post("/api/workshops", json={"name": "My Custom"}).json()["id"]
        payload = b"\xff\xfb\x90\x44" + b"\x00" * 50
        files = {"file": ("song.mp3", io.BytesIO(payload), "audio/mpeg")}
        client.post(f"/api/workshops/{wid}/upload", files=files)
        state = client.get(f"/api/workshops/{wid}").json()
        # 用户命名的不被覆盖
        assert state["WorkshopName"] == "My Custom"
        # 但 raw_audio_file_path 写入了（跨平台用 Path 比较）
        assert _P(state["TabState"]["Tab1"]["RawAudioFilePath"]) == _P("raw_audio/song.mp3")

    def test_upload_to_missing_workshop(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        files = {"file": ("x.mp3", io.BytesIO(b"x"), "audio/mpeg")}
        r = client.post(
            "/api/workshops/nonexistent/upload", files=files
        )
        assert r.status_code == 404


class TestSSE:
    """SSE 端点真实流在 TestClient 下会让 generator 永不返回。为避免挂起，
    这里只验证 *事件总线* 的等价路径。SSE 端点本身走手工 curl 验证。
    """

    def test_bus_emit_propagates_to_subscribers(
        self, kernel_and_client
    ) -> None:
        """验证 EventBus.emit 能让订阅者收到事件（与 SSE 等价语义）。"""
        kernel, client = kernel_and_client  # noqa: F841
        sub_q = kernel.bus.subscribe()
        try:
            kernel.bus.emit("testwid", "test_event", {"k": 1})
            ev = sub_q.get(timeout=2.0)
            assert ev.type == "test_event"
            assert ev.workshop_id == "testwid"
            assert ev.payload == {"k": 1}
        finally:
            kernel.bus.unsubscribe(sub_q)

    def test_sse_route_registered_in_openapi(self, kernel_and_client) -> None:
        """SSE 路由在 OpenAPI schema 里能看到。"""
        _, client = kernel_and_client
        schema = client.get("/openapi.json").json()
        assert "/api/events" in schema["paths"]

    def test_sse_wait_does_not_block_event_loop(
        self, kernel_and_client
    ) -> None:
        kernel, _ = kernel_and_client

        async def is_disconnected() -> bool:
            await asyncio.sleep(0)
            return False

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(kernel=kernel),
            ),
            is_disconnected=is_disconnected,
        )

        async def scenario() -> tuple[str, float]:
            response = await events(request)

            async def emit_soon() -> None:
                await asyncio.sleep(0.05)
                kernel.bus.emit("wid", "test_event", {"ok": True})

            emitter = asyncio.create_task(emit_soon())
            started = time.perf_counter()
            try:
                frame = await asyncio.wait_for(
                    anext(response.body_iterator),
                    timeout=0.2,
                )
            finally:
                await emitter
                await response.body_iterator.aclose()
            return frame, time.perf_counter() - started

        frame, elapsed = asyncio.run(scenario())

        assert '"type": "test_event"' in frame
        assert elapsed < 0.2


# ---------------------------------------------------------------------------
# SSE 端到端 smoke test（separate process 跑）
# ---------------------------------------------------------------------------


def test_sse_real_stream_smoke() -> None:
    """起真 uvicorn 进程，传一个 emit 过去，验证 SSE message 格式。

    用 threading 起 ``uvicorn.Server``，Postman/curl 方式直连。
    MVP smoke：本测试默认会被 pytest -k "not sse_real_stream_smoke" 跳过，除非
    手动运行 ``pytest -k "test_sse_real_stream_smoke"``。
    """
    import socket
    import threading
    import time

    import uvicorn

    from src.kernel.kernel import Kernel
    from src.ui.server import make_app

    # 找空闲端口
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        kernel = Kernel(cache_root=Path(tmp), autosave=False)
        kernel.boot()
        app = make_app(kernel)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        # 等服务起好
        time.sleep(1.5)

        try:
            import httpx

            with httpx.stream(
                "GET",
                f"http://127.0.0.1:{port}/api/events",
                timeout=5.0,
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                wid = kernel.create_workshop("SSE-SMOKE")["id"]
                kernel.bus.emit(
                    wid, "separation_started", {"model": "smoke"}
                )
                # 读若干字节直到拿到我们的消息
                collected = ""
                deadline = time.time() + 3.0
                for chunk in resp.iter_text():
                    collected += chunk
                    if "separation_started" in collected and "smoke" in collected:
                        break
                    if time.time() > deadline:
                        break
                assert "separation_started" in collected
                assert wid in collected
                assert '"smoke"' in collected
        finally:
            server.should_exit = True
            thread.join(timeout=3.0)


class TestMockEndpoints:
    def test_selected_tracks_persist(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)
        ws.state.tab_state.tab2.separation_state = "done"
        ws.state.tab_state.tab2.track_audio_file_path = {
            "vocals": "track_audio/vocals.wav",
            "guitar": "track_audio/guitar.wav",
        }

        r = client.put(
            f"/api/workshops/{wid}/selected-tracks",
            json={"tracks": ["guitar", "vocals"]},
        )

        assert r.status_code == 200
        assert r.json()["tracks"] == ["vocals", "guitar"]
        state = client.get(f"/api/workshops/{wid}").json()
        assert state["TabState"]["Tab2"]["SelectedTracks"] == [
            "vocals",
            "guitar",
        ]

    def test_selected_tracks_reject_unknown_track(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]

        r = client.put(
            f"/api/workshops/{wid}/selected-tracks",
            json={"tracks": ["vocals", "unknown"]},
        )

        assert r.status_code == 409

    def test_selected_tracks_require_completed_separation(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]

        r = client.put(
            f"/api/workshops/{wid}/selected-tracks",
            json={"tracks": ["vocals"]},
        )

        assert r.status_code == 409

    def test_selected_tracks_reject_inactive_workshop(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        old_wid = kernel.create_workshop("Old")["id"]
        kernel.create_workshop("Active")

        r = client.put(
            f"/api/workshops/{old_wid}/selected-tracks",
            json={"tracks": []},
        )

        assert r.status_code == 409

    def test_current_tab_persists(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]

        r = client.put(
            f"/api/workshops/{wid}/current-tab",
            json={"tab": "Tab3"},
        )

        assert r.status_code == 200
        state = client.get(f"/api/workshops/{wid}").json()
        assert state["LastTab"] == "Tab3"

    def test_separate_returns_ok(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        r = client.post(
            f"/api/workshops/{wid}/separate",
            json={"model": "BS-RoFormer"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_analyze_returns_ok(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        r = client.post(
            f"/api/workshops/{wid}/analyze",
            json={"track": "vocals", "plugin": "chord_ismir2019"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_get_analysis_results_reads_persisted_json(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)
        tid = ws.upsert_analysis_task("guitar", "chord_chordnet_2e1d")
        expected = {
            "chords": [{"start": 0.0, "end": 1.0, "chord": "C"}]
        }
        result_abs = ws.cache.save_analysis_result(
            "chord_chordnet_2e1d",
            tid,
            expected,
            ext="json",
        )
        ws.complete_analysis("guitar", tid, ws.cache.to_relative(result_abs))

        r = client.get(f"/api/workshops/{wid}/analysis-results")

        assert r.status_code == 200
        assert r.json()["results"]["guitar"] == expected
        assert (
            r.json()["result_plugins"]["guitar"]
            == "chord_chordnet_2e1d"
        )

    def test_analysis_results_restore_latest_rerun(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)

        for plugin, chord in (
            ("chord_chordnet_2e1d", "C"),
            ("chord_btc_sl", "G"),
            ("chord_chordnet_2e1d", "Am"),
        ):
            tid = ws.upsert_analysis_task("guitar", plugin)
            result = {"chords": [{"chord": chord}]}
            result_abs = ws.cache.save_analysis_result(
                plugin,
                tid,
                result,
                ext="json",
            )
            ws.complete_analysis(
                "guitar",
                tid,
                ws.cache.to_relative(result_abs),
            )

        body = client.get(
            f"/api/workshops/{wid}/analysis-results"
        ).json()

        assert body["results"]["guitar"]["chords"][0]["chord"] == "Am"
        assert (
            body["result_plugins"]["guitar"]
            == "chord_chordnet_2e1d"
        )

    def test_visualization_mock(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        r = client.get(f"/api/workshops/{wid}/visualization?track=full")
        assert r.status_code == 200
        body = r.json()
        assert "waveform" in body or "beats" in body

    def test_visualization_uses_requested_stem_waveform(
        self, kernel_and_client, monkeypatch
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)
        stem_path = ws.cache.track_audio_path("bass", "bass.wav")
        stem_path.parent.mkdir(parents=True, exist_ok=True)
        stem_path.write_bytes(b"stem")
        ws.state.tab_state.tab2.separation_state = "done"
        ws.state.tab_state.tab2.track_audio_file_path = {
            "bass": ws.cache.to_relative(stem_path),
        }
        loaded = {}

        def fake_load(path):
            loaded["path"] = Path(path)
            return SimpleNamespace(
                samples=[0.0, 1.0],
                sample_rate=2,
                duration=1.0,
            )

        monkeypatch.setattr(
            "src.audio.loader.load_audio_multi_channel",
            fake_load,
        )
        monkeypatch.setattr(
            "src.visualizer.waveform.compute_waveform",
            lambda audio, num_frames=2000: SimpleNamespace(
                to_dict=lambda: {
                    "peaks": [0.25, 1.0],
                    "duration": 1.0,
                    "sampleRate": 2,
                    "frameInterval": 0.5,
                    "totalFrames": 2,
                }
            ),
        )

        r = client.get(
            f"/api/workshops/{wid}/visualization?track=bass"
        )

        assert r.status_code == 200
        assert loaded["path"] == stem_path
        assert r.json()["waveform"]["peaks"] == [0.25, 1.0]

    def test_visualization_does_not_generate_fake_chords(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]

        r = client.get(
            f"/api/workshops/{wid}/visualization?track=bass"
        )

        assert r.status_code == 200
        assert r.json()["chords"] == []
        assert r.json()["metadata"]["hasChordData"] is False

    def test_audio_stream(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        r = client.get(f"/api/workshops/{wid}/audio/vocals")
        assert r.status_code == 200
        assert "audio/wav" in r.headers["content-type"]
        assert len(r.content) > 0

    def test_range_request_for_track_audio(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)
        stem_path = ws.cache.track_audio_path("vocals", "vocals.wav")
        stem_path.parent.mkdir(parents=True, exist_ok=True)
        stem_path.write_bytes(bytes(range(64)))
        ws.state.tab_state.tab2.track_audio_file_path = {
            "vocals": ws.cache.to_relative(stem_path),
        }

        r = client.get(
            f"/api/workshops/{wid}/audio/vocals",
            headers={"Range": "bytes=0-15"},
        )

        assert r.status_code == 206
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["content-range"].startswith("bytes 0-15/")
        assert len(r.content) == 16

    def test_midi_export_uses_current_selected_tracks(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)
        ws.state.tab_state.tab2.separation_state = "done"
        ws.state.tab_state.tab2.track_audio_file_path = {
            "bass": "track_audio/track_bass/bass.wav",
            "guitar": "track_audio/track_guitar/guitar.wav",
        }
        ws.set_selected_tracks(["bass", "guitar"])

        for track, chord in (("bass", "C:maj"), ("guitar", "A:min")):
            tid = ws.upsert_analysis_task(track, "chord_test")
            result_path = ws.cache.save_analysis_result(
                "chord_test",
                tid,
                {
                    "chords": [
                        {"start": 0.0, "end": 2.0, "chord": chord},
                    ]
                },
                ext="json",
            )
            ws.complete_analysis(
                track,
                tid,
                ws.cache.to_relative(result_path),
            )

        r = client.get(
            f"/api/workshops/{wid}/midi",
            params=[("tracks", "bass"), ("tracks", "guitar")],
        )

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/midi")
        assert r.content.startswith(b"MThd")
        midi = __import__("pretty_midi").PrettyMIDI(io.BytesIO(r.content))
        assert [item.name for item in midi.instruments] == ["BASS", "GUITAR"]

    def test_midi_export_rejects_unselected_track(
        self, kernel_and_client
    ) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        ws = kernel.manager.get(wid)
        ws.state.tab_state.tab2.separation_state = "done"
        ws.state.tab_state.tab2.track_audio_file_path = {
            "bass": "track_audio/track_bass/bass.wav",
        }
        ws.set_selected_tracks(["bass"])

        r = client.get(
            f"/api/workshops/{wid}/midi",
            params=[("tracks", "guitar")],
        )

        assert r.status_code == 409


class TestErrorFormat:
    def test_404_returns_error_key(
        self, kernel_and_client
    ) -> None:
        _, client = kernel_and_client
        r = client.get("/api/workshops/nope")
        assert r.status_code == 404
        body = r.json()
        assert "detail" in body
        assert "error" in body["detail"]


class TestStaticFiles:
    def test_static_index_html_served(
        self, kernel_and_client
    ) -> None:
        _, client = kernel_and_client
        # js/app.js 路径是否真挂上了
        r = client.get("/static/js/app.js")
        assert r.status_code == 200
        assert "app.js" in r.text or "TABsucks" in r.text
        assert r.headers["cache-control"] == "no-store, max-age=0"

    def test_index_disables_cache(self, kernel_and_client) -> None:
        _, client = kernel_and_client

        r = client.get("/")

        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store, max-age=0"
        assert "app.js?v=20260716g" in r.text

    def test_tab2_and_tab3_have_separate_responsibilities(
        self, kernel_and_client
    ) -> None:
        _, client = kernel_and_client

        html = client.get("/").text

        step2 = html.index('id="step-2"')
        track_selection = html.index('id="stem-selection-list"')
        step3 = html.index('id="step-3"')
        analyzer_config = html.index('id="analysis-config-list"')
        assert step2 < track_selection < step3 < analyzer_config
        assert 'id="phase-analyze"' not in html

    def test_tab4_has_selected_track_timeline_and_playback_controls(
        self, kernel_and_client
    ) -> None:
        _, client = kernel_and_client

        html = client.get("/").text

        step4 = html.index('id="step-4"')
        track_list = html.index('id="tab4-track-list"')
        step4_end = html.index("</section>", step4)
        playback = html.index('id="playback-controls"')
        assert step4 < track_list < step4_end < playback

        app_js = client.get("/static/js/app.js").text
        assert "state.selectedTracks.has(track)" in app_js
        assert "createTab4AudioElements(wid, tracks)" in app_js
        assert "className = 'tab4-playhead'" in app_js
        assert "renderChordBlocks(" in app_js
        assert 'id="tab4-zoom"' in html
        assert 'id="btn-export-midi"' in html
        assert "calculateTimelineLayout" in app_js
        assert "api.exportMidi" in app_js

    def test_frontend_filters_events_and_binds_results_to_plugins(
        self, kernel_and_client
    ) -> None:
        _, client = kernel_and_client

        stream_js = client.get("/static/js/event_stream.js").text
        app_js = client.get("/static/js/app.js").text

        assert "event.workshop_id !== this._wid" in stream_js
        assert "stream.setWorkshopId(wid)" in app_js
        assert "isTrackAnalysisComplete(track)" in app_js
        assert "plugin.name.startsWith('chord_')" in app_js
        assert "plugin.input_stems.includes(track)" in app_js


class TestPluginEndpoints:
    def test_list_separators(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        r = client.get("/api/plugins/separators")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert any(p["name"] == "example_separator" for p in body)

    def test_list_analyzers(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        r = client.get("/api/plugins/analyzers")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert any(p["name"] == "example_analyzer" for p in body)


class TestLastTabPersistence:
    def test_new_workshop_has_tab1(self, kernel_and_client) -> None:
        """新建车间 LastTab 应为 Tab1。"""
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("Fresh")["id"]
        state = client.get(f"/api/workshops/{wid}").json()
        assert state["LastTab"] == "Tab1"

    def test_last_tab_persists_after_close(self, kernel_and_client) -> None:
        """关闭后 LastTab 被保留。"""
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("Persistent")["id"]
        # 模拟 Tab3 激活后再关闭
        ws = kernel.manager.get(wid)
        ws.set_last_tab("Tab3")
        ws.save()
        client.post(f"/api/workshops/{wid}/close")
        # 重新激活后 LastTab 仍在
        client.post(f"/api/workshops/{wid}/switch")
        state = client.get(f"/api/workshops/{wid}").json()
        assert state["LastTab"] == "Tab3"


class TestUploadByUrlErrors:
    def test_empty_url_400(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("ErrTest")["id"]
        r = client.post(
            f"/api/workshops/{wid}/upload-by-url", json={"url": ""}
        )
        assert r.status_code == 400
        assert "URL 不能为空" in str(r.json())

    def test_non_http_url_400(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("ErrTest2")["id"]
        r = client.post(
            f"/api/workshops/{wid}/upload-by-url",
            json={"url": "ftp://evil"},
        )
        assert r.status_code == 400
        assert "http(s)://" in str(r.json())

    def test_missing_wid_404(self, kernel_and_client) -> None:
        _, client = kernel_and_client
        r = client.post(
            "/api/workshops/nonexistent/upload-by-url",
            json={"url": "http://x.com/x.mp3"},
        )
        assert r.status_code == 404
