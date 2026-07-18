"""Tkinter status window for the TABsucks Windows desktop distribution."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import DISABLED, END, NORMAL, StringVar, TclError, Text, Tk, messagebox, ttk

from src.ui.desktop_launcher import DesktopRuntime, UserPaths, resolve_user_paths

logger = logging.getLogger("tabsucks.desktop")


class QueueLogHandler(logging.Handler):
    """Forward formatted log records to the Tk event loop."""

    def __init__(self, target: queue.Queue[str]) -> None:
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)


class LoggingStream:
    """Redirect text written to stdout/stderr into the logging pipeline."""

    encoding = "utf-8"

    def __init__(self, target_logger: logging.Logger, level: int) -> None:
        self.target_logger = target_logger
        self.level = level
        self._buffer = ""

    def write(self, message: str) -> int:
        self._buffer += message
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.target_logger.log(self.level, line.rstrip())
        return len(message)

    def flush(self) -> None:
        if self._buffer.strip():
            self.target_logger.log(self.level, self._buffer.rstrip())
        self._buffer = ""

    def isatty(self) -> bool:
        return False


def configure_logging(
    log_dir: Path,
    message_queue: queue.Queue[str],
) -> Path:
    """Configure UTF-8 file logging and live window logging."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"tabsucks-{timestamp}.log"
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in list(root_logger.handlers):
        if getattr(handler, "_tabsucks_desktop_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._tabsucks_desktop_handler = True  # type: ignore[attr-defined]
    root_logger.addHandler(file_handler)

    queue_handler = QueueLogHandler(message_queue)
    queue_handler.setFormatter(formatter)
    queue_handler._tabsucks_desktop_handler = True  # type: ignore[attr-defined]
    root_logger.addHandler(queue_handler)
    return log_path


def redirect_standard_streams() -> None:
    """Capture plugin print output in the desktop log."""
    stream_logger = logging.getLogger("tabsucks.output")
    sys.stdout = LoggingStream(stream_logger, logging.INFO)
    sys.stderr = LoggingStream(stream_logger, logging.ERROR)


@dataclass(frozen=True)
class WindowEvent:
    kind: str
    value: str = ""


class LauncherWindow:
    """Display startup progress and own the desktop runtime lifecycle."""

    def __init__(
        self,
        root: Tk,
        *,
        runtime: DesktopRuntime,
        paths: UserPaths,
        log_path: Path,
        log_queue: queue.Queue[str],
    ) -> None:
        self.root = root
        self.runtime = runtime
        self.paths = paths
        self.log_path = log_path
        self.log_queue = log_queue
        self.events: queue.Queue[WindowEvent] = queue.Queue()
        self.status = StringVar(value="正在准备 TABsucks...")
        self.address = StringVar(value="服务尚未启动")
        self._closing = False

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.stop_and_close)
        self.root.after(100, self._poll_queues)
        self.root.after(150, self._start_runtime)

    def _build(self) -> None:
        self.root.title("TABsucks")
        self.root.geometry("760x500")
        self.root.minsize(620, 400)

        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)

        ttk.Label(
            container,
            text="TABsucks",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            container,
            textvariable=self.status,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 12))

        address_row = ttk.Frame(container)
        address_row.pack(fill="x")
        ttk.Entry(
            address_row,
            textvariable=self.address,
            state="readonly",
        ).pack(side="left", fill="x", expand=True)
        self.open_button = ttk.Button(
            address_row,
            text="打开界面",
            command=self.open_browser,
            state=DISABLED,
        )
        self.open_button.pack(side="left", padx=(8, 0))
        self.copy_button = ttk.Button(
            address_row,
            text="复制地址",
            command=self.copy_address,
            state=DISABLED,
        )
        self.copy_button.pack(side="left", padx=(8, 0))

        ttk.Separator(container).pack(fill="x", pady=14)
        ttk.Label(container, text="运行日志").pack(anchor="w", pady=(0, 6))

        log_frame = ttk.Frame(container)
        log_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.log_text = Text(
            log_frame,
            wrap="word",
            state=DISABLED,
            font=("Consolas", 9),
            yscrollcommand=scrollbar.set,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(
            actions,
            text="打开日志目录",
            command=self.open_log_directory,
        ).pack(side="left")
        self.stop_button = ttk.Button(
            actions,
            text="停止并退出",
            command=self.stop_and_close,
        )
        self.stop_button.pack(side="right")

    def _start_runtime(self) -> None:
        threading.Thread(
            target=self._runtime_worker,
            name="tabsucks-startup",
            daemon=True,
        ).start()

    def _runtime_worker(self) -> None:
        try:
            logger.info("数据目录: %s", self.paths.root)
            logger.info("日志文件: %s", self.log_path)
            url = self.runtime.start()
            logger.info("TABsucks 已就绪: %s", url)
            self.events.put(WindowEvent("ready", url))
        except Exception as error:  # noqa: BLE001
            logger.exception("TABsucks 启动失败")
            self.events.put(WindowEvent("error", str(error)))

    def _poll_queues(self) -> None:
        self._drain_logs()
        self._drain_events()
        try:
            if self.root.winfo_exists():
                self.root.after(100, self._poll_queues)
        except TclError:
            pass

    def _drain_logs(self) -> None:
        lines: list[str] = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return

        self.log_text.configure(state=NORMAL)
        self.log_text.insert(END, "\n".join(lines) + "\n")
        self.log_text.see(END)
        self.log_text.configure(state=DISABLED)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            if event.kind == "ready":
                self.address.set(event.value)
                self.status.set("TABsucks 正在运行，浏览器已自动打开。")
                self.open_button.configure(state=NORMAL)
                self.copy_button.configure(state=NORMAL)
            elif event.kind == "error":
                self.status.set("启动失败，请查看下方日志。")
                messagebox.showerror(
                    "TABsucks 启动失败",
                    f"{event.value}\n\n详细信息已写入：\n{self.log_path}",
                    parent=self.root,
                )
            elif event.kind == "stopped":
                self.root.destroy()

    def open_browser(self) -> None:
        if self.runtime.url:
            webbrowser.open(self.runtime.url)

    def copy_address(self) -> None:
        if not self.runtime.url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.runtime.url)
        self.status.set("地址已复制到剪贴板。")

    def open_log_directory(self) -> None:
        try:
            os.startfile(self.paths.logs)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror("无法打开日志目录", str(error), parent=self.root)

    def stop_and_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.status.set("正在停止服务并保存数据...")
        self.open_button.configure(state=DISABLED)
        self.copy_button.configure(state=DISABLED)
        self.stop_button.configure(state=DISABLED)
        threading.Thread(
            target=self._stop_worker,
            name="tabsucks-shutdown",
            daemon=True,
        ).start()

    def _stop_worker(self) -> None:
        try:
            self.runtime.stop()
            logger.info("TABsucks 已安全停止")
        except Exception:  # noqa: BLE001
            logger.exception("TABsucks 停止时发生错误")
        finally:
            self.events.put(WindowEvent("stopped"))


def main() -> None:
    """Start the packaged Windows desktop experience."""
    paths = resolve_user_paths()
    paths.create()
    log_queue: queue.Queue[str] = queue.Queue()
    log_path = configure_logging(paths.logs, log_queue)
    redirect_standard_streams()

    root = Tk()
    LauncherWindow(
        root,
        runtime=DesktopRuntime(paths=paths),
        paths=paths,
        log_path=log_path,
        log_queue=log_queue,
    )
    root.mainloop()


__all__ = [
    "LauncherWindow",
    "LoggingStream",
    "QueueLogHandler",
    "configure_logging",
    "main",
    "redirect_standard_streams",
]
