"""Behavior tests for the Windows desktop launcher core."""

from __future__ import annotations

import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.ui.desktop_launcher import (
    ApplicationBundle,
    DesktopRuntime,
    LocalWebServer,
    UserPaths,
    find_available_port,
    resolve_user_paths,
    wait_for_http,
)
from src.ui.desktop_window import LoggingStream


def test_resolve_user_paths_uses_local_app_data(
    monkeypatch,
) -> None:
    local_app_data = Path("C:/Users/Test/AppData/Local")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    paths = resolve_user_paths()

    assert paths == UserPaths(
        root=local_app_data / "TABsucks",
        cache=local_app_data / "TABsucks" / "cache",
        logs=local_app_data / "TABsucks" / "logs",
    )


def test_find_available_port_moves_past_an_occupied_port() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    occupied_port = occupied.getsockname()[1]

    try:
        selected = find_available_port("127.0.0.1", occupied_port)
    finally:
        occupied.close()

    assert selected != occupied_port
    assert selected > 0


def test_wait_for_http_returns_true_after_server_is_ready() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert wait_for_http(url, timeout=2.0) is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_wait_for_http_times_out_when_server_never_starts() -> None:
    assert wait_for_http(
        "http://127.0.0.1:1/",
        timeout=0.1,
        poll_interval=0.02,
    ) is False


def test_local_web_server_starts_and_stops() -> None:
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")

    app = fastapi.FastAPI()

    @app.get("/")
    async def index() -> dict[str, bool]:
        return {"ok": True}

    server = LocalWebServer(
        app,
        host="127.0.0.1",
        port=find_available_port("127.0.0.1", 18000),
    )

    server.start()
    try:
        assert server.wait_until_ready(timeout=3.0) is True
        assert wait_for_http(server.url, timeout=1.0) is True
    finally:
        server.stop(timeout=3.0)

    assert server.is_alive is False


def test_local_web_server_stop_raises_if_thread_remains_alive() -> None:
    class StuckThread:
        def join(self, timeout: float) -> None:
            pass

        def is_alive(self) -> bool:
            return True

    server = LocalWebServer.__new__(LocalWebServer)
    server._thread = StuckThread()
    server._shutdown_event = threading.Event()
    server._server = type(
        "Server",
        (),
        {"should_exit": False, "force_exit": False},
    )()

    with pytest.raises(RuntimeError, match="did not stop"):
        server.stop(timeout=0.01)

    assert server._shutdown_event.is_set()
    assert server._server.should_exit is True
    assert server._server.force_exit is True


def test_desktop_runtime_opens_browser_only_after_server_is_ready(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FakePaths:
        cache = Path("C:/TABsucks/cache")

        def create(self) -> None:
            events.append("paths.create")

    class FakeServer:
        url = "http://127.0.0.1:8123/"

        def __init__(self, app, *, host: str, port: int) -> None:
            events.append(f"server.init:{app}:{host}:{port}")

        def start(self) -> None:
            events.append("server.start")

        def wait_until_ready(self, *, timeout: float) -> bool:
            events.append("server.ready")
            return True

        def stop(self, *, timeout: float) -> None:
            events.append("server.stop")

    monkeypatch.setattr(
        "src.ui.desktop_launcher.find_available_port",
        lambda host, preferred_port: 8123,
    )

    runtime = DesktopRuntime(
        paths=FakePaths(),
        application_factory=lambda cache: ApplicationBundle(
            app="app",
            shutdown=lambda: events.append("app.shutdown"),
        ),
        server_factory=FakeServer,
        browser_open=lambda url: events.append(f"browser.open:{url}") or True,
    )

    assert runtime.start() == "http://127.0.0.1:8123/"
    runtime.stop()

    assert events == [
        "paths.create",
        "server.init:app:127.0.0.1:8123",
        "server.start",
        "server.ready",
        "browser.open:http://127.0.0.1:8123/",
        "server.stop",
        "app.shutdown",
    ]


def test_desktop_runtime_preserves_bundle_when_server_stop_fails() -> None:
    class FakeServer:
        def stop(self, *, timeout: float) -> None:
            raise RuntimeError("still running")

    shutdown_calls: list[str] = []
    runtime = DesktopRuntime()
    runtime._server = FakeServer()
    runtime._bundle = ApplicationBundle(
        app="app",
        shutdown=lambda: shutdown_calls.append("shutdown"),
    )

    with pytest.raises(RuntimeError, match="still running"):
        runtime.stop()

    assert shutdown_calls == []
    assert runtime._server is not None
    assert runtime._bundle is not None


def test_logging_stream_emits_complete_lines(caplog) -> None:
    stream = LoggingStream(logging.getLogger("tabsucks.test.output"), logging.INFO)

    with caplog.at_level(logging.INFO, logger="tabsucks.test.output"):
        stream.write("first")
        stream.write(" line\nsecond")
        stream.flush()

    assert [record.message for record in caplog.records] == [
        "first line",
        "second",
    ]
