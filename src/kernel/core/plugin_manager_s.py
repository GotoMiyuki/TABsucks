"""Compatibility wrapper for the merged PluginManager implementation."""

from __future__ import annotations

from src.kernel.core.plugin_manager import PluginManager, PluginManagerError


class SeparationPluginManager(PluginManager):
    """Backward-compatible alias for older separation-manager imports."""

    pass


SeparationPluginManagerError = PluginManagerError

__all__ = [
    "SeparationPluginManager",
    "SeparationPluginManagerError",
    "PluginManager",
    "PluginManagerError",
]
