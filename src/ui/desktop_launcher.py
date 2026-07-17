"""Windows desktop launcher core for the local TABsucks web application."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


@dataclass(frozen=True)
class UserPaths:
    """Writable per-user directories used by the desktop distribution."""

    root: Path
    cache: Path
    logs: Path

    def create(self) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)


def resolve_user_paths() -> UserPaths:
    """Return Windows-friendly writable directories for application data."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "TABsucks"
    else:
        root = Path.home() / "AppData" / "Local" / "TABsucks"
    return UserPaths(root=root, cache=root / "cache", logs=root / "logs")


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            candidate.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(
    host: str = "127.0.0.1",
    preferred_port: int = 8000,
    *,
    attempts: int = 100,
) -> int:
    """Return the preferred port or the next bindable local port."""
    for port in range(preferred_port, preferred_port + attempts):
        if _can_bind(host, port):
            return port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind((host, 0))
        return int(candidate.getsockname()[1])


def wait_for_http(
    url: str,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.1,
) -> bool:
    """Poll an HTTP URL until it responds or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=min(1.0, timeout)) as response:  # noqa: S310
                if response.status < 500:
                    return True
        except HTTPError as error:
            if error.code < 500:
                return True
        except (OSError, URLError):
            pass
        time.sleep(poll_interval)
    return False


@dataclass(frozen=True)
class ApplicationBundle:
    """Web application and the callback that releases its resources."""

    app: Any
    shutdown: Callable[[], None]


def build_default_application(cache_root: Path) -> ApplicationBundle:
    """Build the production FastAPI application without importing it at startup."""
    if getattr(sys, "frozen", False):
        executable_root = Path(sys.executable).resolve().parent
        internal_root = executable_root / "_internal"
        runtime_root = internal_root if internal_root.is_dir() else executable_root
    else:
        runtime_root = Path(__file__).resolve().parents[2]
    os.chdir(runtime_root)
    os.environ["TABSUCKS_MODEL_CACHE"] = str(cache_root / "models")
    os.environ["TABSUCKS_BUNDLED_MODELS"] = str(runtime_root / "models")

    bundled_ffmpeg = runtime_root / "ffmpeg"
    if bundled_ffmpeg.is_dir():
        os.environ["PATH"] = os.pathsep.join(
            [str(bundled_ffmpeg), os.environ.get("PATH", "")]
        )
    else:
        try:
            import static_ffmpeg

            static_ffmpeg.add_paths()
        except ImportError:
            pass

    from src.kernel.kernel import Kernel
    from src.ui.server import make_app

    kernel = Kernel(cache_root=cache_root)
    kernel.boot()
    return ApplicationBundle(app=make_app(kernel), shutdown=kernel.shutdown)


class LocalWebServer:
    """Run a Uvicorn application in a background thread."""

    def __init__(
        self,
        app: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        import uvicorn

        self.host = host
        self.port = port
        self._shutdown_event = threading.Event()
        app_state = getattr(app, "state", None)
        if app_state is not None:
            app_state.tabsucks_shutdown_event = self._shutdown_event
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=3,
        )
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_alive:
            return
        self._server.should_exit = False
        self._thread = threading.Thread(
            target=self._server.run,
            name="tabsucks-web-server",
            daemon=True,
        )
        self._thread.start()

    def wait_until_ready(self, *, timeout: float = 30.0) -> bool:
        return wait_for_http(self.url, timeout=timeout)

    def stop(self, *, timeout: float = 10.0) -> None:
        if self._thread is None:
            return
        self._shutdown_event.set()
        self._server.should_exit = True
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            self._server.force_exit = True
            self._thread.join(timeout=min(3.0, timeout))
        if self._thread.is_alive():
            raise RuntimeError("TABsucks local service did not stop in time.")
        self._thread = None


class DesktopRuntime:
    """Coordinate user paths, the local server, and browser launch."""

    def __init__(
        self,
        *,
        paths: UserPaths | Any | None = None,
        host: str = "127.0.0.1",
        preferred_port: int = 8000,
        application_factory: Callable[[Path], ApplicationBundle] = build_default_application,
        server_factory: Callable[..., LocalWebServer] = LocalWebServer,
        browser_open: Callable[[str], bool] = webbrowser.open,
    ) -> None:
        self.paths = paths or resolve_user_paths()
        self.host = host
        self.preferred_port = preferred_port
        self._application_factory = application_factory
        self._server_factory = server_factory
        self._browser_open = browser_open
        self._bundle: ApplicationBundle | None = None
        self._server: LocalWebServer | Any | None = None
        self._url: str | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str | None:
        return self._url

    def start(self, *, timeout: float = 30.0) -> str:
        with self._lock:
            if self._url is not None:
                return self._url

            self.paths.create()
            port = find_available_port(self.host, self.preferred_port)
            self._bundle = self._application_factory(self.paths.cache)
            self._server = self._server_factory(
                self._bundle.app,
                host=self.host,
                port=port,
            )
            self._server.start()

            if not self._server.wait_until_ready(timeout=timeout):
                self._stop_unlocked()
                raise RuntimeError("TABsucks local service did not become ready in time.")

            self._url = self._server.url
            self._browser_open(self._url)
            return self._url

    def stop(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            self._stop_unlocked(timeout=timeout)

    def _stop_unlocked(self, *, timeout: float = 10.0) -> None:
        if self._server is not None:
            self._server.stop(timeout=timeout)
        if self._bundle is not None:
            self._bundle.shutdown()
        self._server = None
        self._bundle = None
        self._url = None


__all__ = [
    "ApplicationBundle",
    "DesktopRuntime",
    "LocalWebServer",
    "UserPaths",
    "build_default_application",
    "find_available_port",
    "resolve_user_paths",
    "wait_for_http",
]
