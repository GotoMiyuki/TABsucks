"""PluginManager 测试。"""

from __future__ import annotations

import numpy as np
import pytest

from src.kernel.core.plugin_manager import PluginManager, PluginManagerError
from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


# ---- Mock 插件 ----

class MockRhythmPlugin(Plugin):
    @property
    def name(self) -> str:
        return "rhythm_foundation"

    @property
    def version(self) -> str:
        return "0.0.1-test"

    def execute(self, rc, **kwargs) -> dict:
        return {"status": "success", "data": {"global_bpm": 120.0}}


class MockChordPlugin(Plugin):
    @property
    def name(self) -> str:
        return "chord_test"

    @property
    def version(self) -> str:
        return "0.0.1-test"

    def execute(self, rc, **kwargs) -> dict:
        stem = kwargs.get("stem_name", "piano")
        return {"status": "success", "stem": stem, "data": []}


# ---- 测试 ----

class TestPluginManager:
    """PluginManager 核心功能测试。"""

    def setup_method(self):
        self.rc = ResourceController()
        self.pm = PluginManager(self.rc)

    def test_register_and_get(self) -> None:
        plugin = MockRhythmPlugin()
        self.pm.register(plugin)
        assert self.pm.get("rhythm_foundation") is plugin

    def test_get_nonexistent_returns_none(self) -> None:
        assert self.pm.get("nonexistent") is None

    def test_unregister(self) -> None:
        self.pm.register(MockRhythmPlugin())
        self.pm.unregister("rhythm_foundation")
        assert self.pm.get("rhythm_foundation") is None

    def test_unregister_nonexistent_is_noop(self) -> None:
        self.pm.unregister("nonexistent")  # 不应抛异常

    def test_list_plugins(self) -> None:
        self.pm.register(MockRhythmPlugin())
        self.pm.register(MockChordPlugin())
        names = self.pm.list_plugins()
        assert "rhythm_foundation" in names
        assert "chord_test" in names

    def test_execute_injects_rc(self) -> None:
        """execute 应自动注入 rc，插件能读到 rc 中的数据。"""
        self.rc.set_metadata("sample_rate", 22050)
        self.pm.register(MockRhythmPlugin())
        result = self.pm.execute("rhythm_foundation")
        assert result["status"] == "success"
        assert result["data"]["global_bpm"] == 120.0

    def test_execute_passes_kwargs(self) -> None:
        self.pm.register(MockChordPlugin())
        result = self.pm.execute("chord_test", stem_name="guitar")
        assert result["stem"] == "guitar"

    def test_execute_nonexistent_raises(self) -> None:
        with pytest.raises(PluginManagerError, match="插件不存在"):
            self.pm.execute("nonexistent")

    def test_register_overwrites(self) -> None:
        """同名插件注册应覆盖。"""
        plugin_v1 = MockRhythmPlugin()
        plugin_v2 = MockRhythmPlugin()
        self.pm.register(plugin_v1)
        self.pm.register(plugin_v2)
        assert self.pm.get("rhythm_foundation") is plugin_v2
