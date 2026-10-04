"""Compatibility wrapper for the merged ResourceController implementation."""

from __future__ import annotations

from src.kernel.core.resource_controller import ResourceController, ResourceControllerError


ResourceController_s = ResourceController


__all__ = ["ResourceController_s", "ResourceController", "ResourceControllerError"]
