"""TABsucks UI 服务 CLI 入口。

启动方式::

    python -m src.ui

或者::

    python -m src.ui.cli --host 127.0.0.1 --port 8000

构造 :py:class:`Kernel` 并 :py:meth:`Kernel.boot`，再传给 :py:func:`src.ui.server.make_app`，用
uvicorn 运行。``Kernel.run()`` 不必调用——uvicorn 自己起 server。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

# 确保 static-ffmpeg 的二进制在 PATH 中，供 audio-separator 等使用
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

from src.kernel.core.cache_system import CACHE_ROOT_DEFAULT
from src.kernel.kernel import Kernel
from src.ui.server import make_app


def main(argv: list[str] | None = None) -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(prog="tabsucks-ui")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="uvicorn 监听地址（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="uvicorn 端口（默认 8000）",
    )
    parser.add_argument(
        "--cache-root",
        default=str(CACHE_ROOT_DEFAULT),
        help="cache 根目录（默认 ./cache）",
    )
    parser.add_argument(
        "--no-autosave",
        action="store_true",
        help="关闭 autosave（调试用，单车间高频改动场景不建议）",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="开 dev 模式（代码改动自动 reload，仅开发用）",
    )
    args = parser.parse_args(argv)

    # Windows 中文 codepage fix：Python 默认 cp1252 打印中文会乱码
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, Exception):  # noqa: BLE001
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("tabsucks.cli")

    kernel = Kernel(
        cache_root=Path(args.cache_root),
        autosave=not args.no_autosave,
    )
    loaded, failed = kernel.boot()
    logger.info(
        "Kernel.boot: 加载 %d 车间，失败 %d → http://%s:%d",
        loaded, len(failed), args.host, args.port,
    )
    for _, reason in failed:
        logger.warning("  失败车间: %s", reason)

    app = make_app(kernel)

    if args.reload:
        # ``uvicorn --reload`` 必须 import string。主进程先把 app 注入
        # ``src.ui.server.app``，然后 uvicorn fork 出 reload worker；
        # worker re-import ``src.ui.server`` 时 app 已就绪（且 cache_root 一致）。
        from src.ui import server as _server_module
        _server_module._install_default_app()

        uvicorn.run(
            "src.ui.server:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level="info",
        )
    else:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )


if __name__ == "__main__":
    main()


__all__ = ["main"]
