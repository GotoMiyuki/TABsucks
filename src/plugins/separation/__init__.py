"""Separation plugin package.

Keep this package initializer lightweight. Manifest-backed plugins import
submodules such as ``src.plugins.separation.model_1.separator``; Python executes
this file first, so eager imports here must not point at removed legacy modules.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SeparationPlugin",
    "SeparationResult",
    "SeparatorError",
    "TrackId",
]


def __getattr__(name: str) -> Any:
    """Lazy exports for the current manifest-backed separation plugin."""
    if name in __all__:
        from src.plugins.separation.model_1.separator import (
            SeparationPlugin,
            SeparationResult,
            SeparatorError,
            TrackId,
        )

        exports = {
            "SeparationPlugin": SeparationPlugin,
            "SeparationResult": SeparationResult,
            "SeparatorError": SeparatorError,
            "TrackId": TrackId,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
