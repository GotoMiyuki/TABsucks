"""Resource controller for shared buffers, metadata, models, and VRAM budgets."""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

import numpy as np


class ResourceControllerError(Exception):
    """Raised when a resource operation cannot be completed."""

    pass


class ResourceController:
    """Shared state bus used by plugins and orchestration code.

    This is the canonical controller. It includes the thread-safety and advisory
    VRAM-budgeting behavior that previously lived in ``resource_controller_s``.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._buffers: dict[str, np.ndarray] = {}
        self._metadata: dict[str, Any] = {}
        self._models: dict[str, Any] = {}
        self._device: Any = None
        self._vram_allocations: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Buffers
    # ------------------------------------------------------------------

    def get_buffer(self, name: str) -> np.ndarray:
        """Return a named audio/data buffer.

        Raises:
            ResourceControllerError: when the buffer is missing.
        """
        with self._lock:
            if name not in self._buffers:
                raise ResourceControllerError(f"Buffer '{name}' not found")
            return self._buffers[name]

    def set_buffer(self, name: str, data: np.ndarray) -> None:
        """Write or replace a named buffer."""
        with self._lock:
            self._buffers[name] = data

    def set_buffers_batch(self, mapping: dict[str, np.ndarray]) -> None:
        """Write several buffers while holding one lock."""
        with self._lock:
            self._buffers.update(mapping)

    def get_buffers_batch(self, names: list[str]) -> dict[str, np.ndarray]:
        """Read several buffers while holding one lock."""
        with self._lock:
            return {name: self.get_buffer(name) for name in names}

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_metadata(self, key: str) -> Any:
        """Return metadata value, or ``None`` when it is absent."""
        with self._lock:
            return self._metadata.get(key)

    def set_metadata(self, key: str, value: Any) -> None:
        """Write or replace metadata."""
        with self._lock:
            self._metadata[key] = value

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def get_current_device(self) -> Any:
        """Return the current inference device, lazily detected."""
        with self._lock:
            if self._device is not None:
                return self._device

            try:
                import torch

                self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            except ImportError:
                self._device = "cpu"
            return self._device

    def request_model(self, name: str, loader: Callable[[str], Any]) -> Any:
        """Load a model through ``loader`` or return the cached instance."""
        with self._lock:
            if name not in self._models:
                self._models[name] = loader(name)
            return self._models[name]

    def release_model(self, name: str) -> None:
        """Unload a named cached model. Missing names are ignored."""
        with self._lock:
            self._models.pop(name, None)

    def release_all_models(self) -> None:
        """Unload all cached models."""
        with self._lock:
            self._models.clear()

    # ------------------------------------------------------------------
    # GPU / VRAM
    # ------------------------------------------------------------------

    @staticmethod
    def get_gpu_info() -> dict[str, Any]:
        """Return a lightweight GPU memory snapshot."""
        info: dict[str, Any] = {
            "cuda_available": False,
            "device_count": 0,
            "device_name": None,
            "free_mb": None,
            "total_mb": None,
            "used_mb": None,
        }
        try:
            import torch

            if torch.cuda.is_available():
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                info.update(
                    {
                        "cuda_available": True,
                        "device_count": torch.cuda.device_count(),
                        "device_name": torch.cuda.get_device_name(0),
                        "free_mb": free_bytes / (1024**2),
                        "total_mb": total_bytes / (1024**2),
                        "used_mb": (total_bytes - free_bytes) / (1024**2),
                    }
                )
        except ImportError:
            pass
        return info

    def allocate_vram(
        self,
        requester: str,
        amount_mb: float,
        *,
        auto_release: bool = True,
    ) -> dict[str, Any]:
        """Reserve a VRAM budget before running a heavy plugin.

        The reservation is advisory. It clears cached models when requested,
        checks free GPU memory when CUDA is available, and records the requester
        so upper layers can inspect and release the budget later.
        """
        if auto_release:
            self.release_all_models()

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        gpu_info = self.get_gpu_info()
        free_mb = gpu_info["free_mb"]
        total_mb = gpu_info["total_mb"]

        if not gpu_info["cuda_available"]:
            with self._lock:
                self._vram_allocations[requester] = amount_mb
            return {
                "granted": True,
                "requester": requester,
                "requested_mb": amount_mb,
                "free_mb": None,
                "total_mb": None,
                "message": "CUDA is not available; using CPU mode.",
            }

        if free_mb is not None and free_mb < amount_mb:
            return {
                "granted": False,
                "requester": requester,
                "requested_mb": amount_mb,
                "free_mb": free_mb,
                "total_mb": total_mb,
                "message": (
                    f"Not enough VRAM: free {free_mb:.0f} MB, "
                    f"requested {amount_mb:.0f} MB."
                ),
            }

        with self._lock:
            self._vram_allocations[requester] = amount_mb
        return {
            "granted": True,
            "requester": requester,
            "requested_mb": amount_mb,
            "free_mb": free_mb,
            "total_mb": total_mb,
            "message": (
                f"Reserved {amount_mb:.0f} MB VRAM for {requester}; "
                f"free {free_mb:.0f} MB / total {total_mb:.0f} MB."
            ),
        }

    def release_vram(self, requester: str) -> dict[str, Any]:
        """Release a previously recorded VRAM reservation."""
        with self._lock:
            was_allocated = self._vram_allocations.pop(requester, None)

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        if was_allocated is not None:
            return {
                "released": True,
                "was_allocated_mb": was_allocated,
                "message": f"Released {was_allocated:.0f} MB VRAM for {requester}.",
            }
        return {
            "released": False,
            "was_allocated_mb": None,
            "message": f"No VRAM reservation found for {requester}.",
        }

    def release_all_vram(self) -> int:
        """Release all advisory VRAM reservations and return their count."""
        with self._lock:
            count = len(self._vram_allocations)
            self._vram_allocations.clear()

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        return count

    @property
    def vram_status(self) -> dict[str, Any]:
        """Return current advisory VRAM reservations plus GPU status."""
        with self._lock:
            allocations = dict(self._vram_allocations)
        return {
            "allocations": allocations,
            "total_allocated_mb": sum(allocations.values()),
            "gpu": self.get_gpu_info(),
        }

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear all buffers, metadata, cached models, and VRAM reservations."""
        with self._lock:
            self._buffers.clear()
            self._metadata.clear()
            self._models.clear()
            self._vram_allocations.clear()
