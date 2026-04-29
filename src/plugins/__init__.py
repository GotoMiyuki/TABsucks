"""插件系统模块，支持通过 PluginManager 加载拓展插件。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


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
    def execute(self, audio_data, **kwargs) -> dict:
        """执行插件分析。

        Args:
            audio_data: AudioData 对象。
            **kwargs: 额外参数。

        Returns:
            分析结果字典。
        """
        ...


class PluginManager:
    """插件管理器，负责插件的注册和调用。"""

    def __init__(self) -> None:
        """初始化插件管理器。"""
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        """注册插件。"""
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        """注销插件。"""
        if name in self._plugins:
            del self._plugins[name]

    def get(self, name: str) -> Plugin | None:
        """根据名称获取插件。"""
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """列出所有已注册插件。"""
        return list(self._plugins.keys())

    def execute(self, name: str, audio_data, **kwargs) -> dict:
        """执行指定插件。"""
        plugin = self.get(name)
        if plugin is None:
            raise KeyError(f"插件不存在: {name}")
        return plugin.execute(audio_data, **kwargs)
