"""PyInstaller entry point for the TABsucks Windows desktop launcher."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _run_internal_script() -> bool:
    if len(sys.argv) < 3 or sys.argv[1] != "--tabsucks-run-script":
        return False

    script = Path(sys.argv[2]).resolve()
    sys.argv = [str(script), *sys.argv[3:]]
    os.chdir(script.parent)
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name="__main__")
    return True


if __name__ == "__main__" and not _run_internal_script():
    from src.ui.desktop_window import main

    main()
