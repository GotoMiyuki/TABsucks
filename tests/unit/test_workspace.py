"""车间模块测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.workspace import (
    TrackState,
    Workspace,
    WorkspaceManager,
)


class TestTrackState:
    """TrackState 测试。"""

    def test_defaults(self) -> None:
        """默认状态正确。"""
        ts = TrackState(track_id="vocals")
        assert ts.muted is False
        assert ts.solo is False
        assert ts.volume == 1.0

    def test_custom_values(self) -> None:
        """可自定义初始值。"""
        ts = TrackState(track_id="drums", muted=True, volume=0.5)
        assert ts.muted is True
        assert ts.volume == 0.5


class TestWorkspace:
    """Workspace 车间测试。"""

    def test_default_init(self) -> None:
        """默认初始化创建 6 轨状态。"""
        ws = Workspace()
        assert ws.name == "新建车间"
        assert len(ws.track_states) == 6
        assert "vocals" in ws.track_states
        assert "drums" in ws.track_states

    def test_set_track_muted(self) -> None:
        """set_track_muted 正确更新状态。"""
        ws = Workspace()
        ws.set_track_muted("vocals", True)
        assert ws.track_states["vocals"].muted is True

    def test_set_track_solo(self) -> None:
        """set_track_solo 正确更新状态。"""
        ws = Workspace()
        ws.set_track_solo("bass", True)
        assert ws.track_states["bass"].solo is True

    def test_to_dict(self) -> None:
        """to_dict 返回可序列化的字典。"""
        ws = Workspace(name="Test Workspace")
        d = ws.to_dict()
        assert d["name"] == "Test Workspace"
        assert "track_states" in d
        assert "vocals" in d["track_states"]

    def test_from_dict_roundtrip(self) -> None:
        """from_dict -> to_dict 往返一致。"""
        original = Workspace(name="Roundtrip Test")
        original.set_track_muted("piano", True)
        original.set_track_solo("guitar", True)

        d = original.to_dict()
        restored = Workspace.from_dict(d)

        assert restored.name == original.name
        assert restored.track_states["piano"].muted is True
        assert restored.track_states["guitar"].solo is True

    def test_save_and_load(self, tmp_path: Path) -> None:
        """save 和 load 应往返一致。"""
        ws = Workspace(name="Saved Workspace")
        ws.set_track_muted("other", True)

        path = tmp_path / "workspace.json"
        ws.save(path)

        loaded = Workspace.load(path)
        assert loaded.name == ws.name
        assert loaded.track_states["other"].muted is True


class TestWorkspaceManager:
    """WorkspaceManager 管理器测试。"""

    def test_create(self) -> None:
        """create 创建新车间并设为活动状态。"""
        mgr = WorkspaceManager()
        ws = mgr.create("My Workspace")
        assert ws.name == "My Workspace"
        assert mgr.get_active() == ws

    def test_switch_to_existing(self) -> None:
        """switch_to 切换到已存在的车间。"""
        mgr = WorkspaceManager()
        ws1 = mgr.create("Workspace 1")
        ws2 = mgr.create("Workspace 2")
        assert mgr.switch_to(ws1.id) is True
        assert mgr.get_active() == ws1

    def test_switch_to_nonexistent(self) -> None:
        """switch_to 到不存在的 ID 返回 False。"""
        mgr = WorkspaceManager()
        assert mgr.switch_to("nonexistent-id") is False

    def test_list_workspaces(self) -> None:
        """list_workspaces 返回所有车间。"""
        mgr = WorkspaceManager()
        mgr.create("A")
        mgr.create("B")
        workspaces = mgr.list_workspaces()
        assert len(workspaces) == 2

    def test_close(self) -> None:
        """close 删除指定车间。"""
        mgr = WorkspaceManager()
        ws = mgr.create("To Delete")
        assert mgr.close(ws.id) is True
        assert ws.id not in {w.id for w in mgr.list_workspaces()}

    def test_close_active_switches(self) -> None:
        """关闭当前活动车间后，自动切换到其他车间。"""
        mgr = WorkspaceManager()
        ws1 = mgr.create("First")
        ws2 = mgr.create("Second")
        mgr.switch_to(ws1.id)
        mgr.close(ws1.id)
        # 应自动切换到 ws2
        assert mgr.get_active() == ws2
