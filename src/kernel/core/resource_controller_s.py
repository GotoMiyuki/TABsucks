"""
ResourceController_s – 带显存管理与线程安全的资源控制器。

在 ResourceController 基类之上扩展：
- 线程安全：buffer / metadata 读写受 ``threading.RLock`` 保护，
  杜绝分离线程与 UI 线程间的竞态条件。
- 显存分配器：``allocate_vram()`` 在执行前清场 + 校验余量，
  杜绝 OOM 崩溃。``release_vram()`` 在执行后归还配额。
- GPU 探针：``get_gpu_info()`` 统一提供显存查询接口。

与 SeparationPluginManager 的协作::

    rc = ResourceController_s()

    # 1. 显存申请（最核心一步）
    status = rc.allocate_vram("separation_bs_roformer", amount_mb=4096)
    if not status["granted"]:
        raise ResourceControllerError(status["message"])

    # 2. 安全写入（自动加锁）
    rc.set_buffer("raw", audio_array)     # 主线程写入原始音频
    # ... 分离线程读取 ...
    raw = rc.get_buffer("raw")            # 插件线程安全读取

    # 3. 插件执行后回写
    rc.set_buffer("vocals", vocals_data)  # 互斥锁保护
    rc.set_buffer("drums", drums_data)

    # 4. 显存释放
    rc.release_vram("separation_bs_roformer")
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from src.kernel.core.resource_controller import (
    ResourceController,
    ResourceControllerError,
)


class ResourceController_s(ResourceController):
    """带显存管理与线程安全的资源控制器。

    相比基类新增:
    - 所有 buffer / metadata 读写受 ``threading.RLock`` 保护
    - ``allocate_vram()`` / ``release_vram()`` 显存配额管理
    - ``get_gpu_info()`` 统一 GPU 探针
    - ``vram_status`` 属性查看当前分配情况
    """

    def __init__(self) -> None:
        super().__init__()
        # 可重入锁：同一线程多次获取不会死锁
        self._lock = threading.RLock()
        # 显存分配登记: requester_name -> amount_mb
        self._vram_allocations: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 线程安全的 Buffer 操作（覆盖基类）
    # ------------------------------------------------------------------

    def get_buffer(self, name: str) -> np.ndarray:
        """线程安全地获取命名 buffer。

        与基类行为完全一致，额外加上读锁保护。
        """
        with self._lock:
            return super().get_buffer(name)

    def set_buffer(self, name: str, data: np.ndarray) -> None:
        """线程安全地写入命名 buffer。

        写入时持有写锁，保证 AnalysisEngine 回写分离结果时
        不会被 UI 轮询读到半写入的数组。
        """
        with self._lock:
            super().set_buffer(name, data)

    # ------------------------------------------------------------------
    # 线程安全的 Metadata 操作（覆盖基类）
    # ------------------------------------------------------------------

    def get_metadata(self, key: str) -> Any:
        """线程安全地读取元数据。"""
        with self._lock:
            return super().get_metadata(key)

    def set_metadata(self, key: str, value: Any) -> None:
        """线程安全地写入元数据。"""
        with self._lock:
            super().set_metadata(key, value)

    # ------------------------------------------------------------------
    # 批量安全操作
    # ------------------------------------------------------------------

    def set_buffers_batch(self, mapping: dict[str, np.ndarray]) -> None:
        """一次锁内写入多个 buffer，避免逐条加锁的开销。

        适用于分离完成后一次性回写 6 轨数据的场景。
        """
        with self._lock:
            for name, data in mapping.items():
                super().set_buffer(name, data)

    def get_buffers_batch(self, names: list[str]) -> dict[str, np.ndarray]:
        """一次锁内读取多个 buffer。"""
        with self._lock:
            return {name: super().get_buffer(name) for name in names}

    # ------------------------------------------------------------------
    # GPU 探针
    # ------------------------------------------------------------------

    @staticmethod
    def get_gpu_info() -> dict[str, Any]:
        """探测当前 GPU 状态。

        与 SeparationPluginManager 的探测逻辑独立，
        供任何需要显存信息的模块使用。

        Returns:
            {
                "cuda_available": bool,
                "device_count": int,
                "device_name": str | None,
                "free_mb": float | None,
                "total_mb": float | None,
                "used_mb": float | None,
            }
        """
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
                info["cuda_available"] = True
                info["device_count"] = torch.cuda.device_count()
                info["device_name"] = torch.cuda.get_device_name(0)
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                info["free_mb"] = free_bytes / (1024**2)
                info["total_mb"] = total_bytes / (1024**2)
                info["used_mb"] = (total_bytes - free_bytes) / (1024**2)
        except ImportError:
            pass

        return info

    # ------------------------------------------------------------------
    # 显存分配管理
    # ------------------------------------------------------------------

    def allocate_vram(
        self,
        requester: str,
        amount_mb: float,
        *,
        auto_release: bool = True,
    ) -> dict[str, Any]:
        """申请显存配额。

        核心流程:
        1. 若 ``auto_release=True``（默认），先调用
           ``release_all_models()`` 清空所有已缓存模型，
           释放被其他插件占用的显存。
        2. 调用 ``torch.cuda.empty_cache()`` 回收 PyTorch 缓存碎片。
        3. 探测当前空闲显存，与 ``amount_mb`` 对比。
        4. 若余量不足，拒绝申请并返回详细信息。
        5. 若余量充足，登记配额并返回批准。

        这是杜绝 OOM 的最关键步骤 —— 在加载重量级分离模型
        （如 BS-RoFormer ~4GB）之前"清场"。

        Args:
            requester: 申请者标识（如 ``"separation_bs_roformer"``）。
            amount_mb: 申请显存量 (MB)。
            auto_release: 是否在申请前自动释放所有已缓存模型。

        Returns:
            {
                "granted": bool,            # 是否批准
                "requester": str,           # 申请者
                "requested_mb": float,      # 申请量
                "free_mb": float | None,    # 批准时空闲显存
                "total_mb": float | None,   # GPU 总显存
                "message": str,             # 人类可读描述
            }
        """
        # Step 1: 清场
        if auto_release:
            with self._lock:
                self._models.clear()

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        # Step 2: 探测
        gpu_info = self.get_gpu_info()
        free_mb = gpu_info["free_mb"]
        total_mb = gpu_info["total_mb"]

        # Step 3: 判定
        if not gpu_info["cuda_available"]:
            # CPU 模式：不阻塞，但给出提示
            self._vram_allocations[requester] = amount_mb
            return {
                "granted": True,
                "requester": requester,
                "requested_mb": amount_mb,
                "free_mb": None,
                "total_mb": None,
                "message": "CUDA 不可用，将在 CPU 上运行（速度较慢）。",
            }

        if free_mb is not None and free_mb < amount_mb:
            # 显存不足，拒绝
            return {
                "granted": False,
                "requester": requester,
                "requested_mb": amount_mb,
                "free_mb": free_mb,
                "total_mb": total_mb,
                "message": (
                    f"显存不足: 空闲 {free_mb:.0f} MB，"
                    f"申请 {amount_mb:.0f} MB。"
                    f"建议关闭其他应用程序、启用 CPU 模式，"
                    f"或减少模型精度。"
                ),
            }

        # Step 4: 批准
        self._vram_allocations[requester] = amount_mb
        return {
            "granted": True,
            "requester": requester,
            "requested_mb": amount_mb,
            "free_mb": free_mb,
            "total_mb": total_mb,
            "message": (
                f"显存已分配: {amount_mb:.0f} MB 给 '{requester}'，"
                f"当前空闲 {free_mb:.0f} MB / 总计 {total_mb:.0f} MB。"
            ),
        }

    def release_vram(self, requester: str) -> dict[str, Any]:
        """释放指定申请者的显存配额。

        通常在分离运算完成后调用，将配额归还。
        同时调用 ``torch.cuda.empty_cache()`` 回收碎片。

        Args:
            requester: 申请者标识。

        Returns:
            {"released": bool, "was_allocated_mb": float | None, "message": str}
        """
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
                "message": f"'{requester}' 的 {was_allocated:.0f} MB 显存配额已释放。",
            }
        return {
            "released": False,
            "was_allocated_mb": None,
            "message": f"'{requester}' 没有已分配的显存配额。",
        }

    def release_all_vram(self) -> int:
        """释放所有显存配额，返回释放的申请者数量。"""
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
        """当前显存分配状态快照。"""
        gpu_info = self.get_gpu_info()
        return {
            "allocations": dict(self._vram_allocations),
            "total_allocated_mb": sum(self._vram_allocations.values()),
            "gpu": gpu_info,
        }

    # ------------------------------------------------------------------
    # 线程安全的模型操作（覆盖基类）
    # ------------------------------------------------------------------

    def request_model(self, name: str, loader) -> Any:
        """线程安全地请求加载模型。"""
        with self._lock:
            return super().request_model(name, loader)

    def release_model(self, name: str) -> None:
        """线程安全地卸载模型。"""
        with self._lock:
            super().release_model(name)

    def release_all_models(self) -> None:
        """线程安全地卸载所有模型。"""
        with self._lock:
            super().release_all_models()

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空所有状态（线程安全）。"""
        with self._lock:
            super().clear()
            self._vram_allocations.clear()
