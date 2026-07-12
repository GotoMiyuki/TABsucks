"""HTTP server (UI API) 端到端测试。

用 :py:class:`fastapi.testclient.TestClient` 替代真正 uvicorn，启动 < 1 秒。
所有测试用一个 Fresh Kernel（tmp_path cache），互不污染。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.kernel.kernel import Kernel
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

    def test_visualization_mock(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        r = client.get(f"/api/workshops/{wid}/visualization?track=full")
        assert r.status_code == 200
        body = r.json()
        assert "waveform" in body or "beats" in body

    def test_audio_stream(self, kernel_and_client) -> None:
        kernel, client = kernel_and_client
        wid = kernel.create_workshop("X")["id"]
        r = client.get(f"/api/workshops/{wid}/audio/vocals")
        assert r.status_code == 200
        assert "audio/wav" in r.headers["content-type"]
        assert len(r.content) > 0


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
