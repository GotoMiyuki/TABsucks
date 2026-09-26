"""Deprecated import facade; new code uses MusicWorkshop/WorkshopManager."""

from src.kernel.legacy.workspace import TrackState, Workspace, WorkspaceManager

__all__ = ["Workspace", "WorkspaceManager", "TrackState"]
