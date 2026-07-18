"""Compatibility wrapper for the merged ResourceController implementation."""

from __future__ import annotations

from src.kernel.core.resource_controller import ResourceController, ResourceControllerError


class ResourceController_s(ResourceController):
    """Backward-compatible alias for older imports."""

    pass


__all__ = ["ResourceController_s", "ResourceController", "ResourceControllerError"]
