"""插件管理器，负责插件的注册、发现和调度。"""

from __future__ import annotations

from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


class PluginManagerError(Exception):
    """插件管理器操作失败时抛出。"""

    pass


class PluginManager:
    """插件管理器，AnalysisEngine 通过它调度所有插件。

    使用方式::

        rc = ResourceController()
        pm = PluginManager(rc)
        pm.register(FoundationRhythmPlugin())
        result = pm.execute("rhythm_foundation")
    """

    def __init__(self, rc: ResourceController) -> None:
        self._rc = rc
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        """注册插件。同名插件会被覆盖。"""
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        """注销插件。不存在时静默忽略。"""
        self._plugins.pop(name, None)

    def get(self, name: str) -> Plugin | None:
        """根据名称获取插件，不存在时返回 None。"""
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """列出所有已注册插件名称。"""
        return list(self._plugins.keys())

    def execute(self, name: str, **kwargs) -> dict:
        """执行指定插件，自动注入 ResourceController。
        注意不要与AnalysisEngine耦合。

        Args:
            name: 插件名称。
            **kwargs: 传递给插件 execute 的额外参数。

        Returns:
            插件返回的结果字典。

        Raises:
            PluginManagerError: 插件不存在。
        """
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginManagerError(f"插件不存在: {name}")
        return plugin.execute(self._rc, **kwargs)
