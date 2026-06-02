"""核心业务逻辑模块。"""

def __getattr__(name: str):
    """延迟导入，避免在不需要时加载重量级依赖。"""
    if name == "Workspace" or name == "WorkspaceManager":
        from src.kernel.core.workspace import Workspace, WorkspaceManager

        return Workspace if name == "Workspace" else WorkspaceManager
    if name == "MidiExporter" or name == "export_to_midi":
        from src.kernel.core.midi_exporter import MidiExporter, export_to_midi

        return MidiExporter if name == "MidiExporter" else export_to_midi
    if name == "ResourceController":
        from src.kernel.core.resource_controller import ResourceController

        return ResourceController
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Workspace",
    "WorkspaceManager",
    "MidiExporter",
    "export_to_midi",
    "ResourceController",
]
