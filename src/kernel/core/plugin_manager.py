"""Plugin manager for registration, manifest discovery, and execution."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


class PluginManagerError(Exception):
    """Raised when plugin manager operations fail."""

    pass


_PKG_IMPORT_MAP: dict[str, str] = {
    "audio-separator": "audio_separator",
    "onnxruntime-gpu": "onnxruntime",
    "scikit-learn": "sklearn",
}


def _try_import(package_name: str) -> bool:
    import_name = _PKG_IMPORT_MAP.get(package_name, package_name.replace("-", "_"))
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


class PluginManager:
    """Manage plugin instances and lightweight manifest metadata.

    This is now the canonical plugin manager. It includes the separation
    manifest discovery and compatibility checks that previously lived in
    ``plugin_manager_s`` while keeping the original register/get/execute API.
    """

    _DEFAULT_MANIFEST_SUBDIRS: tuple[tuple[str, ...], ...] = (
        ("plugins", "separation"),
    )

    def __init__(self, rc: ResourceController, *, auto_discover: bool = True) -> None:
        self._rc = rc
        self._plugins: dict[str, Plugin] = {}
        self._manifests: dict[str, dict[str, Any]] = {}
        if auto_discover:
            self.refresh_manifests()

    # ------------------------------------------------------------------
    # Registered plugin instances
    # ------------------------------------------------------------------

    def register(self, plugin: Plugin) -> None:
        """Register a plugin instance. Same-name plugins overwrite old ones."""
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        """Unregister a plugin. Missing names are ignored."""
        self._plugins.pop(name, None)

    def get(self, name: str) -> Plugin | None:
        """Return a registered plugin instance, or ``None``."""
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """List registered plugin instance names."""
        return list(self._plugins.keys())

    def execute(self, name: str, **kwargs) -> dict:
        """Execute a registered plugin and inject the ResourceController."""
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginManagerError(f"插件不存在: {name}")
        return plugin.execute(self._rc, **kwargs)

    # ------------------------------------------------------------------
    # Manifest discovery
    # ------------------------------------------------------------------

    def _src_dir(self) -> Path:
        # plugin_manager.py -> src/kernel/core -> src/kernel -> src
        return Path(__file__).resolve().parents[2]

    def refresh_manifests(self) -> int:
        """Rescan known manifest directories and return discovered plugin count."""
        self._manifests.clear()
        src_dir = self._src_dir()

        for subdir in self._DEFAULT_MANIFEST_SUBDIRS:
            search_dir = src_dir.joinpath(*subdir)
            if not search_dir.is_dir():
                continue
            for manifest_path in sorted(search_dir.glob("*/manifest.json")):
                self._load_manifest_file(manifest_path)

        return len(self._manifests)

    def _load_manifest_file(self, manifest_path: Path) -> None:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        plugins_list = data.get("plugins", [])
        if not isinstance(plugins_list, list):
            return

        for raw_entry in plugins_list:
            if not isinstance(raw_entry, dict):
                continue
            name = raw_entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            entry = dict(raw_entry)
            entry["_manifest_dir"] = str(manifest_path.parent)
            self._manifests[name] = entry

    def get_manifest(self, name: str) -> dict[str, Any] | None:
        """Return raw manifest metadata for a plugin name."""
        return self._manifests.get(name)

    def get_available_plugins(self, *, phase: str | None = None) -> list[dict[str, Any]]:
        """Return discovered manifest entries for UI/API display."""
        result: list[dict[str, Any]] = []
        for manifest in self._manifests.values():
            if phase is not None and manifest.get("phase") != phase:
                continue
            result.append({k: v for k, v in manifest.items() if not k.startswith("_")})
        return result

    # ------------------------------------------------------------------
    # Manifest-backed plugin instantiation
    # ------------------------------------------------------------------

    def instantiate_plugin(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> Plugin:
        """Import and instantiate a manifest-backed plugin, then register it."""
        manifest = self._manifests.get(name)
        if manifest is None:
            available = list(self._manifests.keys())
            raise PluginManagerError(f"插件 '{name}' 未在 manifest 中找到。可用插件: {available}")

        entrypoint = manifest.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint:
            raise PluginManagerError(f"插件 '{name}' 的 manifest 缺少 entrypoint")

        class_name = manifest.get("class")
        if not isinstance(class_name, str) or not class_name:
            raise PluginManagerError(f"插件 '{name}' 的 manifest 缺少 class")

        try:
            module = importlib.import_module(entrypoint)
        except ImportError as e:
            raise PluginManagerError(
                f"无法导入插件 '{name}' 的入口模块 '{entrypoint}': {e}"
            ) from e

        try:
            plugin_cls = getattr(module, class_name)
        except AttributeError as e:
            raise PluginManagerError(
                f"模块 '{entrypoint}' 中未找到类 '{class_name}'"
            ) from e

        config = config or {}
        try:
            instance = plugin_cls(**config)
        except TypeError as e:
            raise PluginManagerError(
                f"实例化 '{class_name}' 失败，config={config}: {e}"
            ) from e

        self.register(instance)
        return instance

    def ensure_plugin(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> Plugin | None:
        """Return a registered plugin or instantiate it from a manifest if possible."""
        plugin = self.get(name)
        if plugin is not None:
            return plugin
        if self.get_manifest(name) is None:
            return None
        return self.instantiate_plugin(name, config=config)

    # ------------------------------------------------------------------
    # Compatibility and VRAM preparation
    # ------------------------------------------------------------------

    def check_compatibility(self, name: str) -> dict[str, Any]:
        """Check hardware and Python dependencies declared by a manifest."""
        manifest = self._manifests.get(name)
        if manifest is None:
            return {
                "compatible": False,
                "errors": [f"插件 '{name}' 未在 manifest 中找到"],
                "warnings": [],
            }

        reqs: dict[str, Any] = manifest.get("requirements", {})
        gpu_required_mb = float(reqs.get("gpu_memory_mb", 0) or 0)
        ram_required_mb = float(reqs.get("ram_mb_min", 0) or 0)
        required_packages = list(reqs.get("python_packages", []) or [])

        warnings: list[str] = []
        errors: list[str] = []

        gpu_info = self._rc.get_gpu_info()
        gpu_free_mb = gpu_info["free_mb"]
        gpu_available = bool(gpu_info["cuda_available"])
        gpu_ok = True

        if gpu_required_mb > 0:
            if gpu_available and gpu_free_mb is not None:
                if gpu_free_mb < gpu_required_mb:
                    gpu_ok = False
                    warnings.append(
                        f"GPU free memory {gpu_free_mb:.0f} MB is below "
                        f"plugin requirement {gpu_required_mb:.0f} MB."
                    )
            else:
                warnings.append(
                    f"Plugin requests {gpu_required_mb:.0f} MB VRAM, "
                    "but CUDA is not available; CPU fallback may be slow."
                )

        ram_ok = True
        try:
            import psutil

            avail_mb = psutil.virtual_memory().available / (1024**2)
            if ram_required_mb > 0 and avail_mb < ram_required_mb:
                ram_ok = False
                warnings.append(
                    f"System RAM {avail_mb:.0f} MB is below "
                    f"plugin requirement {ram_required_mb:.0f} MB."
                )
        except ImportError:
            pass

        missing_packages = [
            pkg for pkg in required_packages if isinstance(pkg, str) and not _try_import(pkg)
        ]
        if missing_packages:
            warnings.append(f"Missing Python packages: {missing_packages}")

        compatible = gpu_ok and ram_ok and not missing_packages and not errors

        return {
            "compatible": compatible,
            "gpu_available": gpu_available,
            "gpu_free_mb": gpu_free_mb,
            "gpu_required_mb": gpu_required_mb,
            "gpu_ok": gpu_ok,
            "ram_ok": ram_ok,
            "missing_packages": missing_packages,
            "warnings": warnings,
            "errors": errors,
        }

    def prepare_vram(self, name: str) -> dict[str, Any]:
        """Prepare an advisory VRAM budget for a manifest-backed plugin."""
        manifest = self._manifests.get(name)
        if manifest is None:
            return {
                "ready": False,
                "gpu_free_mb": None,
                "gpu_required_mb": 0.0,
                "models_released": False,
                "message": f"插件 '{name}' 未在 manifest 中找到",
            }

        gpu_required_mb = float(
            manifest.get("requirements", {}).get("gpu_memory_mb", 0) or 0
        )
        allocation = self._rc.allocate_vram(name, gpu_required_mb)
        return {
            "ready": bool(allocation.get("granted")),
            "gpu_free_mb": allocation.get("free_mb"),
            "gpu_required_mb": gpu_required_mb,
            "models_released": True,
            "message": allocation.get("message", ""),
        }


# Backward-compatible public name while callers migrate off plugin_manager_s.
SeparationPluginManager = PluginManager
