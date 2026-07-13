"""资源控制器，管理音频 buffer、元数据和模型的生命周期。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


class ResourceControllerError(Exception):
    """资源控制器操作失败时抛出。"""

    pass


class ResourceController:
    """资源控制器，插件通过它获取音频数据、读写元数据、调度模型。

    使用方式::

        rc = ResourceController()
        rc.set_buffer("raw", audio_array)
        rc.set_metadata("sample_rate", 22050)

        # 插件侧
        raw = rc.get_buffer("raw")
        sr = rc.get_metadata("sample_rate") or 22050
    """

    def __init__(self) -> None:
        self._buffers: dict[str, np.ndarray] = {}
        self._metadata: dict[str, Any] = {}
        self._models: dict[str, Any] = {}
        self._device: Any = None

    # ------------------------------------------------------------------
    # Buffer 操作
    # ------------------------------------------------------------------

    def get_buffer(self, name: str) -> np.ndarray:
        """获取命名 buffer。

        Args:
            name: buffer 名称，如 ``"raw"``、``"bass"``、``"piano"``。

        Returns:
            对应的 numpy 数组。

        Raises:
            ResourceControllerError: buffer 不存在。
        """
        if name not in self._buffers:
            raise ResourceControllerError(f"Buffer '{name}' not found")
        return self._buffers[name]

    def set_buffer(self, name: str, data: np.ndarray) -> None:
        """写入或覆盖命名 buffer。"""
        self._buffers[name] = data

    # ------------------------------------------------------------------
    # Metadata 操作
    # ------------------------------------------------------------------

    def get_metadata(self, key: str) -> Any:
        """获取元数据值，不存在时返回 ``None``。"""
        return self._metadata.get(key)

    def set_metadata(self, key: str, value: Any) -> None:
        """写入或覆盖元数据。"""
        self._metadata[key] = value

    # ------------------------------------------------------------------
    # 模型生命周期
    # ------------------------------------------------------------------

    def get_current_device(self) -> Any:
        """返回当前推理设备（CUDA 或 CPU）。"""
        if self._device is not None:
            return self._device

        try:
            import torch

            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        except ImportError:
            self._device = "cpu"
        return self._device

    def request_model(self, name: str, loader: Callable[[str], Any]) -> Any:
        """请求加载模型，已缓存则直接返回。

        Args:
            name: 模型标识，如 ``"stem_chordformer"``。
            loader: 回调函数，接收模型路径并返回已加载的模型对象。

        Returns:
            模型对象。
        """
        if name not in self._models:
            self._models[name] = loader(name)
        return self._models[name]

    def release_model(self, name: str) -> None:
        """卸载指定模型，释放显存。不存在时静默忽略。"""
        self._models.pop(name, None)

    def release_all_models(self) -> None:
        """卸载所有已加载模型。"""
        self._models.clear()

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空所有 buffer、元数据和模型。"""
        self._buffers.clear()
        self._metadata.clear()
        self._models.clear()
