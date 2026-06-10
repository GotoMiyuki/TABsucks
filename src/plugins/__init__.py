"""插件系统模块：Plugin 基类定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.kernel.core.resource_controller import ResourceController


class Plugin(ABC):
    """插件基类，所有分析插件需继承此类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称。"""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """插件版本。"""
        ...

    @abstractmethod
    def execute(self, rc: ResourceController, **kwargs) -> dict:
        """执行插件分析。

        Args:
            rc: ResourceController 实例，插件通过它读写 buffer/metadata。
            **kwargs: 额外参数（如 stem_name）。

        Returns:
            分析结果字典，至少包含 "status" 键。
        """
        ...


# 部分插件以 BasePlugin 引用此类，保留别名
BasePlugin = Plugin