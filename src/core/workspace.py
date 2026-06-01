"""车间管理模块：一个车间 = 一段音频 + 四个 Tab 的状态。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.audio.loader import AudioData


@dataclass
class TrackState:
    """单个音轨的播放状态。"""

    track_id: str
    muted: bool = False
    solo: bool = False
    volume: float = 1.0  # 0.0 ~ 1.0


@dataclass
class Workspace:
    """音乐车间，包含一段音频及其所有分析结果和 UI 状态。

    一个 Workspace 对应用户界面中的一个"车间 Tab"。
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "新建车间"
    audio_path: str | None = None
    # 分离后的 6 轨状态
    track_states: dict[str, TrackState] = field(default_factory=dict)
    # 分析结果缓存
    _beat_info: dict | None = field(default=None, repr=False)
    _chord_events: list | None = field(default=None, repr=False)
    _separation_result: dict | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """初始化默认的 6 轨状态。"""
        if not self.track_states:
            track_ids = ["vocals", "drums", "bass", "piano", "guitar", "other"]
            self.track_states = {
                tid: TrackState(track_id=tid) for tid in track_ids
            }

    def set_track_muted(self, track_id: str, muted: bool) -> None:
        """设置音轨静音状态。"""
        if track_id in self.track_states:
            self.track_states[track_id].muted = muted

    def set_track_solo(self, track_id: str, solo: bool) -> None:
        """设置音轨独奏状态。"""
        if track_id in self.track_states:
            self.track_states[track_id].solo = solo

    def to_dict(self) -> dict:
        """序列化为字典（用于保存到文件）。"""
        return {
            "id": self.id,
            "name": self.name,
            "audio_path": self.audio_path,
            "track_states": {
                tid: {"muted": ts.muted, "solo": ts.solo, "volume": ts.volume}
                for tid, ts in self.track_states.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> Workspace:
        """从字典反序列化。"""
        ws = cls(
            id=data["id"],
            name=data["name"],
            audio_path=data.get("audio_path"),
        )
        ws.track_states = {
            tid: TrackState(track_id=tid, **ts_data)
            for tid, ts_data in data.get("track_states", {}).items()
        }
        return ws

    def save(self, path: str | Path) -> None:
        """保存车间到 JSON 文件。"""
        path = Path(path)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str | Path) -> Workspace:
        """从 JSON 文件加载车间。"""
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


class WorkspaceManager:
    """车间管理器，负责多个车间的创建、切换和持久化。"""

    def __init__(self) -> None:
        """初始化管理器。"""
        self._workspaces: dict[str, Workspace] = {}
        self._active_id: str | None = None

    def create(self, name: str = "新建车间") -> Workspace:
        """创建新车间。"""
        ws = Workspace(name=name)
        self._workspaces[ws.id] = ws
        self._active_id = ws.id
        return ws

    def get_active(self) -> Workspace | None:
        """获取当前活动车间。"""
        if self._active_id is None:
            return None
        return self._workspaces.get(self._active_id)

    def switch_to(self, workspace_id: str) -> bool:
        """切换到指定车间。"""
        if workspace_id in self._workspaces:
            self._active_id = workspace_id
            return True
        return False

    def list_workspaces(self) -> list[Workspace]:
        """列出所有车间。"""
        return list(self._workspaces.values())

    def close(self, workspace_id: str) -> bool:
        """关闭并删除指定车间。"""
        if workspace_id in self._workspaces:
            del self._workspaces[workspace_id]
            if self._active_id == workspace_id:
                self._active_ids = next(
                    iter(self._workspaces.keys()), None
                )
            return True
        return False
