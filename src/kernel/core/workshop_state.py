"""Validated persistent workshop state and value objects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from .state_migrations import CURRENT_SCHEMA_VERSION, migrate_state


class WorkshopError(Exception):
    """车间模块根异常。"""


class ValidationError(WorkshopError):
    """state.json 字段不合法时抛，message 含字段路径。"""


class WorkshopNotFoundError(WorkshopError):
    """WorkshopManager 找不到指定 ID 时抛。"""


class TaskIdCollisionError(WorkshopError):
    """自动生成 task_id 与已有冲突（极小概率）。"""


# ---------------------------------------------------------------------------
# Tab 状态 dataclass（依据会议 state.json schema）
# ---------------------------------------------------------------------------

#: ``LastTab`` 字段允许值
TabName = Literal["Tab1", "Tab2", "Tab3", "Tab4"]

#: 分离 / 分析运行状态
RunState = Literal["not_started", "running", "done", "failed", "cancelled", "interrupted"]

#: 默认缓存目录由 cache_system 决定，这里不再定义
DEFAULT_WORKSHOP_NAME: str = "New Workshop"
DEFAULT_TAB: TabName = "Tab1"

#: 分析结果子键名（state.json 用 camelCase Key 名，与会议 schema 一致）
KEY_RAW_AUDIO_FILE_PATH: str = "RawAudioFilePath"
KEY_SEPARATION_STATE: str = "SeparationState"
KEY_SEPARATION_MODEL_NAME: str = "SeparationModelName"
KEY_SEPARATION_MODEL_PATH: str = "SeparationModelPath"
KEY_TRACK_AUDIO_FILE_PATH: str = "TrackAudioFilePath"
KEY_SELECTED_TRACKS: str = "SelectedTracks"
KEY_ANALYSIS_TOOL_NAME: str = "AnalysisToolName"
KEY_ANALYSIS_STATE: str = "AnalysisState"
KEY_ANALYSIS_RESULT_PATH: str = "AnalysisResultPath"
KEY_ANALYSIS_TASK_ID: str = "AnalysisTaskId"
KEY_SELECTED_ANALYSIS_RESULT_PATH: str = "SelectedAnalysisResultPath"
KEY_MIX_STATE: str = "MixState"

TRACK_NAMES: tuple[str, ...] = (
    "vocals",
    "drums",
    "bass",
    "piano",
    "guitar",
    "other",
)


@dataclass
class MixState:
    """Tab4 单条音轨的混音状态。"""

    volume: float = 1.0
    mute: bool = False
    solo: bool = False

    def __post_init__(self) -> None:
        """约束 volume 在 [0, 1]。"""
        if not isinstance(self.volume, (int, float)):
            raise ValidationError(f"volume 必须是数字，得到 {type(self.volume).__name__}")
        if not 0.0 <= float(self.volume) <= 1.0:
            raise ValidationError(f"volume 必须 ∈ [0,1]，得到 {self.volume}")

    def to_dict(self) -> dict[str, Any]:
        """→ JSON 字典。"""
        return {"volume": float(self.volume), "mute": self.mute, "solo": self.solo}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MixState:
        """JSON 字典 → 实例（缺少字段则用默认值；多余字段忽略）。"""
        return cls(
            volume=float(d.get("volume", 1.0)),
            mute=bool(d.get("mute", False)),
            solo=bool(d.get("solo", False)),
        )


@dataclass
class Tab1State:
    """Tab1（音频输入）：只持原音频相对路径。"""

    raw_audio_file_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {KEY_RAW_AUDIO_FILE_PATH: self.raw_audio_file_path}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tab1State:
        raw = d.get(KEY_RAW_AUDIO_FILE_PATH)
        if raw is not None and not isinstance(raw, str):
            raise ValidationError(f"Tab1.{KEY_RAW_AUDIO_FILE_PATH} 必须是字符串或 null")
        return cls(raw_audio_file_path=raw)


@dataclass
class Tab2State:
    """Tab2（音轨分离）。"""

    separation_state: RunState = "not_started"
    separation_model_name: str | None = None
    separation_model_path: str | None = None
    #: ``{track_name: relative_path}`` —— 相对 workshop_dir
    track_audio_file_path: dict[str, str] = field(default_factory=dict)
    selected_tracks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            KEY_SEPARATION_STATE: self.separation_state,
            KEY_SEPARATION_MODEL_NAME: self.separation_model_name,
            KEY_SEPARATION_MODEL_PATH: self.separation_model_path,
            KEY_TRACK_AUDIO_FILE_PATH: dict(self.track_audio_file_path),
            KEY_SELECTED_TRACKS: list(self.selected_tracks),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tab2State:
        state = d.get(KEY_SEPARATION_STATE, "not_started")
        if state not in ("not_started", "running", "done", "failed", "cancelled", "interrupted"):
            raise ValidationError(f"Tab2.{KEY_SEPARATION_STATE} 非法: {state!r}")
        track_paths = d.get(KEY_TRACK_AUDIO_FILE_PATH, {})
        if not isinstance(track_paths, dict):
            raise ValidationError(f"Tab2.{KEY_TRACK_AUDIO_FILE_PATH} 必须是 dict")
        for name, p in track_paths.items():
            if not isinstance(name, str) or not isinstance(p, str):
                raise ValidationError(f"Tab2.{KEY_TRACK_AUDIO_FILE_PATH} 内项必须为 str -> str")
        model_name = d.get(KEY_SEPARATION_MODEL_NAME)
        model_path = d.get(KEY_SEPARATION_MODEL_PATH)
        selected_tracks = d.get(KEY_SELECTED_TRACKS, [])
        if not isinstance(selected_tracks, list) or any(
            not isinstance(track, str) or track not in TRACK_NAMES for track in selected_tracks
        ):
            raise ValidationError(f"Tab2.{KEY_SELECTED_TRACKS} 必须是合法音轨名组成的 list")
        if model_name is not None and not isinstance(model_name, str):
            raise ValidationError(f"Tab2.{KEY_SEPARATION_MODEL_NAME} 必须是字符串或 null")
        if model_path is not None and not isinstance(model_path, str):
            raise ValidationError(f"Tab2.{KEY_SEPARATION_MODEL_PATH} 必须是字符串或 null")
        return cls(
            separation_state=state,
            separation_model_name=model_name,
            separation_model_path=model_path,
            track_audio_file_path=dict(track_paths),
            selected_tracks=[track for track in TRACK_NAMES if track in selected_tracks],
        )


@dataclass
class Tab3TrackState:
    """Tab3 单条音轨的一个分析任务。

    同一音轨可对应多个 task（多次跑 / 不同工具），所以用 ``task_id`` 作为
    子键，这里 ``Tab3`` 外层 dict 用 ``f"{track_name}::{task_id}"`` 作复合键。
    """

    analysis_tool_name: str | None = None
    analysis_state: RunState = "not_started"
    analysis_result_path: str | None = None
    analysis_task_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            KEY_ANALYSIS_TOOL_NAME: self.analysis_tool_name,
            KEY_ANALYSIS_STATE: self.analysis_state,
            KEY_ANALYSIS_RESULT_PATH: self.analysis_result_path,
            KEY_ANALYSIS_TASK_ID: self.analysis_task_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tab3TrackState:
        state = d.get(KEY_ANALYSIS_STATE, "not_started")
        if state not in ("not_started", "running", "done", "failed", "cancelled", "interrupted"):
            raise ValidationError(f"{KEY_ANALYSIS_STATE} 非法: {state!r}")
        for key in (
            KEY_ANALYSIS_TOOL_NAME,
            KEY_ANALYSIS_RESULT_PATH,
            KEY_ANALYSIS_TASK_ID,
        ):
            v = d.get(key)
            if v is not None and not isinstance(v, str):
                raise ValidationError(f"{key} 必须是字符串或 null")
        return cls(
            analysis_tool_name=d.get(KEY_ANALYSIS_TOOL_NAME),
            analysis_state=state,
            analysis_result_path=d.get(KEY_ANALYSIS_RESULT_PATH),
            analysis_task_id=d.get(KEY_ANALYSIS_TASK_ID),
        )


@dataclass
class Tab4TrackState:
    """Tab4 单条音轨状态：选中的分析结果 + 混音控制。"""

    selected_analysis_result_path: str | None = None
    mix_state: MixState = field(default_factory=MixState)

    def to_dict(self) -> dict[str, Any]:
        return {
            KEY_SELECTED_ANALYSIS_RESULT_PATH: self.selected_analysis_result_path,
            KEY_MIX_STATE: self.mix_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tab4TrackState:
        sel = d.get(KEY_SELECTED_ANALYSIS_RESULT_PATH)
        if sel is not None and not isinstance(sel, str):
            raise ValidationError(f"{KEY_SELECTED_ANALYSIS_RESULT_PATH} 必须是字符串或 null")
        mix_raw = d.get(KEY_MIX_STATE, {})
        if not isinstance(mix_raw, dict):
            raise ValidationError(f"{KEY_MIX_STATE} 必须是 dict")
        return cls(
            selected_analysis_result_path=sel,
            mix_state=MixState.from_dict(mix_raw),
        )


@dataclass
class TabState:
    """4 个 Tab 的完整状态（对应 state.json 的 TabState 字段）。"""

    tab1: Tab1State = field(default_factory=Tab1State)
    tab2: Tab2State = field(default_factory=Tab2State)
    #: ``{f"{track_name}::{task_id}": Tab3TrackState}``
    tab3: dict[str, Tab3TrackState] = field(default_factory=dict)
    #: ``{track_name: Tab4TrackState}``
    tab4: dict[str, Tab4TrackState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "Tab1": self.tab1.to_dict(),
            "Tab2": self.tab2.to_dict(),
            "Tab3": {k: v.to_dict() for k, v in self.tab3.items()},
            "Tab4": {k: v.to_dict() for k, v in self.tab4.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TabState:
        tab1 = Tab1State.from_dict(d.get("Tab1", {}) or {})
        tab2 = Tab2State.from_dict(d.get("Tab2", {}) or {})
        raw_tab3 = d.get("Tab3", {}) or {}
        if not isinstance(raw_tab3, dict):
            raise ValidationError("Tab3 必须是 dict")
        tab3: dict[str, Tab3TrackState] = {
            k: Tab3TrackState.from_dict(v) for k, v in raw_tab3.items()
        }
        raw_tab4 = d.get("Tab4", {}) or {}
        if not isinstance(raw_tab4, dict):
            raise ValidationError("Tab4 必须是 dict")
        tab4: dict[str, Tab4TrackState] = {
            k: Tab4TrackState.from_dict(v) for k, v in raw_tab4.items()
        }
        return cls(tab1=tab1, tab2=tab2, tab3=tab3, tab4=tab4)


@dataclass
class WorkshopState:
    """完整 state.json 的 dataclass 表示。"""

    workshop_name: str = DEFAULT_WORKSHOP_NAME
    last_tab: TabName = DEFAULT_TAB
    tab_state: TabState = field(default_factory=TabState)

    # ---- 双向转换 ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "WorkshopName": self.workshop_name,
            "SchemaVersion": CURRENT_SCHEMA_VERSION,
            "LastTab": self.last_tab,
            "TabState": self.tab_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkshopState:
        """JSON 字典 → 实例。校验失败抛 :py:class:`ValidationError`。"""
        if not isinstance(d, dict):
            raise ValidationError("state 根必须是 dict")
        try:
            d = migrate_state(d)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        name = d.get("WorkshopName", DEFAULT_WORKSHOP_NAME)
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("WorkshopName 必须是非空字符串")
        last = d.get("LastTab", DEFAULT_TAB)
        if last not in ("Tab1", "Tab2", "Tab3", "Tab4"):
            raise ValidationError(f"LastTab 非法: {last!r}")
        raw_tab_state = d.get("TabState", {})
        if not isinstance(raw_tab_state, dict):
            raise ValidationError("TabState 必须是 dict")
        try:
            tabs = TabState.from_dict(raw_tab_state)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValidationError(f"TabState: {error}") from error
        return cls(workshop_name=name, last_tab=last, tab_state=tabs)

    def validate(self) -> None:
        """业务级校验，目前主要检查嵌套字段（from_dict 已经覆盖大部分）。"""
        # 这里保留为钩子，方便后续扩展：例如检查 RawAudioFilePath 是否在
        # 合理范围内等。
        return None


# ---------------------------------------------------------------------------
# 复合键工具（Tab3 同 track 多 task 时用）
# ---------------------------------------------------------------------------


def tab3_key(track_name: str, task_id: str) -> str:
    """生成 Tab3 复合键。"""
    return f"{track_name}::{task_id}"


def parse_tab3_key(key: str) -> tuple[str, str]:
    """解析 Tab3 复合键 → ``(track_name, task_id)``。

    Raises:
        ValidationError: 格式不合法。
    """
    if "::" not in key:
        raise ValidationError(f"Tab3 key 格式非法: {key!r}")
    name, _, task_id = key.partition("::")
    if not name or not task_id:
        raise ValidationError(f"Tab3 key 含空字段: {key!r}")
    return name, task_id


def new_task_id() -> str:
    """生成新 task_id（8 位 hex）。"""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# 音乐车间运行时
# ---------------------------------------------------------------------------
