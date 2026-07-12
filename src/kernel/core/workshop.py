"""音乐车间（Music Workshop）业务模块。

依据 ``docs/meetings/2026-6-16-meeting.md``：

* §1 数据流 + 文件系统设计（落盘格式 + state.json schema）
* §4 车间是"黏合各层级、规范工作流程"的胶水
* 状态变更后由 :py:meth:`MusicWorkshop.save` 立即落盘，混合类操作延迟落盘
* 坏掉的 state.json **跳过**（Obsidian 风格），不影响其它车间

设计要点：

* 运行时表示 :py:class:`MusicWorkshop` 与持久化 :py:class:`WorkshopState` 分开，
  避免裸 dict 操作引入隐患。
* 所有路径（``RawAudioFilePath``、``TrackAudioFilePath`` 等）使用相对路径
  （以 ``workshop_<id>/`` 为基准），跨设备同步不失效。
* EventBus 是 **可选依赖**：构造器传 ``None`` 时静默，不发事件（便于单测）。
* 5 秒 debounced autosave 守护意外退出，最坏情况丢失最近 5 秒改动。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from src.kernel.core.cache_system import WorkshopCache
    from src.kernel.kernel import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


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
RunState = Literal["not_started", "running", "done", "failed"]

#: 默认缓存目录由 cache_system 决定，这里不再定义
DEFAULT_WORKSHOP_NAME: str = "New Workshop"
DEFAULT_TAB: TabName = "Tab1"

#: 分析结果子键名（state.json 用 camelCase Key 名，与会议 schema 一致）
KEY_RAW_AUDIO_FILE_PATH: str = "RawAudioFilePath"
KEY_SEPARATION_STATE: str = "SeparationState"
KEY_SEPARATION_MODEL_NAME: str = "SeparationModelName"
KEY_SEPARATION_MODEL_PATH: str = "SeparationModelPath"
KEY_TRACK_AUDIO_FILE_PATH: str = "TrackAudioFilePath"
KEY_ANALYSIS_TOOL_NAME: str = "AnalysisToolName"
KEY_ANALYSIS_STATE: str = "AnalysisState"
KEY_ANALYSIS_RESULT_PATH: str = "AnalysisResultPath"
KEY_ANALYSIS_TASK_ID: str = "AnalysisTaskId"
KEY_SELECTED_ANALYSIS_RESULT_PATH: str = "SelectedAnalysisResultPath"
KEY_MIX_STATE: str = "MixState"


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
            raise ValidationError(
                f"Tab1.{KEY_RAW_AUDIO_FILE_PATH} 必须是字符串或 null"
            )
        return cls(raw_audio_file_path=raw)


@dataclass
class Tab2State:
    """Tab2（音轨分离）。"""

    separation_state: RunState = "not_started"
    separation_model_name: str | None = None
    separation_model_path: str | None = None
    #: ``{track_name: relative_path}`` —— 相对 workshop_dir
    track_audio_file_path: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            KEY_SEPARATION_STATE: self.separation_state,
            KEY_SEPARATION_MODEL_NAME: self.separation_model_name,
            KEY_SEPARATION_MODEL_PATH: self.separation_model_path,
            KEY_TRACK_AUDIO_FILE_PATH: dict(self.track_audio_file_path),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Tab2State:
        state = d.get(KEY_SEPARATION_STATE, "not_started")
        if state not in ("not_started", "running", "done", "failed"):
            raise ValidationError(f"Tab2.{KEY_SEPARATION_STATE} 非法: {state!r}")
        track_paths = d.get(KEY_TRACK_AUDIO_FILE_PATH, {})
        if not isinstance(track_paths, dict):
            raise ValidationError(
                f"Tab2.{KEY_TRACK_AUDIO_FILE_PATH} 必须是 dict"
            )
        for name, p in track_paths.items():
            if not isinstance(name, str) or not isinstance(p, str):
                raise ValidationError(
                    f"Tab2.{KEY_TRACK_AUDIO_FILE_PATH} 内项必须为 str -> str"
                )
        model_name = d.get(KEY_SEPARATION_MODEL_NAME)
        model_path = d.get(KEY_SEPARATION_MODEL_PATH)
        if model_name is not None and not isinstance(model_name, str):
            raise ValidationError(
                f"Tab2.{KEY_SEPARATION_MODEL_NAME} 必须是字符串或 null"
            )
        if model_path is not None and not isinstance(model_path, str):
            raise ValidationError(
                f"Tab2.{KEY_SEPARATION_MODEL_PATH} 必须是字符串或 null"
            )
        return cls(
            separation_state=state,
            separation_model_name=model_name,
            separation_model_path=model_path,
            track_audio_file_path=dict(track_paths),
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
        if state not in ("not_started", "running", "done", "failed"):
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
            raise ValidationError(
                f"{KEY_SELECTED_ANALYSIS_RESULT_PATH} 必须是字符串或 null"
            )
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
            "LastTab": self.last_tab,
            "TabState": self.tab_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkshopState:
        """JSON 字典 → 实例。校验失败抛 :py:class:`ValidationError`。"""
        if not isinstance(d, dict):
            raise ValidationError("state 根必须是 dict")
        name = d.get("WorkshopName", DEFAULT_WORKSHOP_NAME)
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("WorkshopName 必须是非空字符串")
        last = d.get("LastTab", DEFAULT_TAB)
        if last not in ("Tab1", "Tab2", "Tab3", "Tab4"):
            raise ValidationError(f"LastTab 非法: {last!r}")
        raw_tab_state = d.get("TabState", {})
        if not isinstance(raw_tab_state, dict):
            raise ValidationError("TabState 必须是 dict")
        return cls(
            workshop_name=name,
            last_tab=last,
            tab_state=TabState.from_dict(raw_tab_state),
        )

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


class MusicWorkshop:
    """单一车间的运行时表示。

    边界：

    * 持有 :py:class:`WorkshopState`（业务状态）
    * 持有 :py:class:`~cache_system.WorkshopCache`（IO 通道）
    * 可选持有 :py:class:`~kernel.EventBus` 引用（``None`` 时不发事件）
    * **不**自动调用 SeparatorPlugin / AnalysisPlugin；那是编排层职责

    落盘策略：

    * 业务方法默认 **不立即 flush**，只标 dirty；5 秒 debounce 后自动落盘
    * 关键操作（完成分离 / 完成分析 / 切换 active / 关闭）会 **立即** save
    """

    # 默认 autosave 间隔（秒）
    AUTOSAVE_INTERVAL: float = 5.0

    def __init__(
        self,
        workshop_id: str,
        cache: WorkshopCache,
        state: WorkshopState | None = None,
        event_bus: EventBus | None = None,
        autosave: bool = True,
    ) -> None:
        self.id: str = workshop_id
        self._cache = cache
        self._state = state or WorkshopState()
        self._bus = event_bus
        self._dirty: bool = False
        self._lock = threading.RLock()
        self._autosave_enabled = autosave
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        if self._autosave_enabled:
            self._start_autosave_thread()

    # ------------------------------------------------------------------
    # 内部：autosave 线程
    # ------------------------------------------------------------------

    def _start_autosave_thread(self) -> None:
        """启动 5 秒 debounce 的后台线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._autosave_loop,
            name=f"workshop-autosave-{self.id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _autosave_loop(self) -> None:
        """每 ``AUTOSAVE_INTERVAL`` 秒检查 dirty，flush 一次。

        守护线程：主进程退出时自动结束。
        """
        while not self._stop_event.is_set():
            # wait 返回 True 表示等到事件，False 表示超时
            signaled = self._stop_event.wait(self.AUTOSAVE_INTERVAL)
            if signaled:
                return
            with self._lock:
                if self._dirty:
                    try:
                        self._cache.save_state(self._state.to_dict())
                        self._dirty = False
                        self._emit("state_saved", {"reason": "autosave"})
                    except OSError as e:
                        logger.warning(
                            "workshop[%s] autosave 失败: %s", self.id, e
                        )

    def _mark_dirty(self) -> None:
        """任何 set_* 操作末尾调用：标 dirty + 触发本车间事件（如有）。"""
        with self._lock:
            self._dirty = True

    def stop_autosave(self) -> None:
        """停止后台线程（最多等 1 秒）。

        原子写保兜底：老 state.json 即使线程未干净退出也不会损坏。
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            # 无论是否真退出，把引用清空（防止重复 join 同一线程句柄）
            self._thread = None

    def resume_autosave(self) -> None:
        """重新启动 autosave 线程（用户重新激活本车间时用）。

        幂等：已开着则 noop。用户禁用 (``autosave=False`` 构造) 也不开。
        """
        if not self._autosave_enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._start_autosave_thread()

    # ------------------------------------------------------------------
    # 内部：事件
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """发事件；bus 为 None 时静默。"""
        if self._bus is None:
            return
        try:
            self._bus.emit(self.id, event_type, payload)
        except AttributeError:
            # bus 接口不匹配时静默，避免业务层异常
            logger.debug("EventBus 接口不匹配，忽略 event=%s", event_type)

    # ------------------------------------------------------------------
    # 公共只读属性
    # ------------------------------------------------------------------

    @property
    def state(self) -> WorkshopState:
        """当前状态（只读引用，修改内部 dict 不直接标 dirty）。"""
        return self._state

    @property
    def name(self) -> str:
        return self._state.workshop_name

    @property
    def last_tab(self) -> TabName:
        return self._state.last_tab

    @property
    def cache(self) -> WorkshopCache:
        return self._cache

    # ------------------------------------------------------------------
    # Tab1 — 音频输入
    # ------------------------------------------------------------------

    def set_raw_audio(
        self,
        src_path: Path | str,
        dst_filename: str | None = None,
    ) -> Path:
        """复制原音频到 cache/raw_audio/ 并写入 Tab1 路径。

        **自动命名**：如果当前车间名仍是 :py:data:`DEFAULT_WORKSHOP_NAME`，
        用 ``Path(dst_filename or src_path.name).stem`` 重命名（只触发一次）。

        Args:
            src_path: 外部音频文件路径。
            dst_filename: 落盘文件名；不传则保留原文件名。

        Returns:
            落盘后的绝对路径。
        """
        with self._lock:
            abs_path = self._cache.save_raw_audio(src_path, dst_filename)
            rel = self._cache.to_relative(abs_path)
            self._state.tab_state.tab1.raw_audio_file_path = rel
            self._mark_dirty()
            # 自动命名（仅当车间名还是默认值）
            self._maybe_auto_name(Path(rel).name)
            self.save()  # 关键路径立即落盘
            self._emit("raw_audio_set", {"path": rel})
            return abs_path

    def set_raw_audio_from_bytes(
        self,
        data: bytes,
        dst_filename: str,
    ) -> Path:
        """URL 下载流场景：直接落盘字节流到 ``raw_audio/``。

        同样会触发自动命名。
        """
        with self._lock:
            abs_path = self._cache.save_raw_audio_from_bytes(data, dst_filename)
            rel = self._cache.to_relative(abs_path)
            self._state.tab_state.tab1.raw_audio_file_path = rel
            self._mark_dirty()
            self._maybe_auto_name(Path(rel).name)
            self.save()
            self._emit("raw_audio_set", {"path": rel})
            return abs_path

    def _maybe_auto_name(self, filename: str) -> None:
        """如果当前车间名仍是默认，自动用 ``Path(filename).stem`` 改名。

        尊重用户：用户主动 :py:meth:`rename` 之后再调 set_raw_audio 不会触发覆盖。
        """
        if self._state.workshop_name != DEFAULT_WORKSHOP_NAME:
            return
        stem = Path(filename).stem.strip()
        if stem and stem != self._state.workshop_name:
            self._state.workshop_name = stem

    def get_raw_audio_path(self) -> Path | None:
        """读取原音频绝对路径；未设置返回 None。"""
        rel = self._state.tab_state.tab1.raw_audio_file_path
        if rel is None:
            return None
        try:
            return self._cache.to_absolute(rel)
        except ValueError as e:
            logger.warning("RawAudioFilePath 不合法: %s", e)
            return None

    def set_last_tab(self, tab: TabName) -> None:
        """记录当前 UI 所在的 Tab（让重启后能恢复）。"""
        with self._lock:
            if self._state.last_tab == tab:
                return
            self._state.last_tab = tab
            self._mark_dirty()
            # 不立即 save，5 秒后 autosave 即可

    def rename(self, new_name: str) -> None:
        """重命名车间。"""
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValidationError("车间名必须是非空字符串")
        with self._lock:
            self._state.workshop_name = new_name
            self._mark_dirty()
            self.save()

    # ------------------------------------------------------------------
    # Tab2 — 音轨分离
    # ------------------------------------------------------------------

    def start_separation(
        self,
        model_name: str,
        model_path: str | None = None,
    ) -> None:
        """分离开始：标 running + emit。"""
        with self._lock:
            self._state.tab_state.tab2.separation_state = "running"
            self._state.tab_state.tab2.separation_model_name = model_name
            self._state.tab_state.tab2.separation_model_path = model_path
            self._mark_dirty()
            self.save()
            self._emit("separation_started", {"model": model_name})

    def complete_separation(self, track_files_rel: dict[str, str]) -> None:
        """分离完成：写入每条 stem 路径（**相对** workshop_dir） + 标 done + emit。

        Args:
            track_files_rel: ``{track_name: relative_path}``，相对路径以
                ``workshop_<id>/`` 为基准。业务方在写完 stem 到 cache/track_audio/
                后，应调用 :py:meth:`WorkshopCache.to_relative` 自己转一次，
                再传给本方法。设计理由：避免 workshop 做隐式路径转换，调用方
                显式负责"路径属于 cache 内"这一不变量。
        """
        with self._lock:
            # 验证所有相对路径确实在 workshop_dir 内（防御性）
            for rel in track_files_rel.values():
                _ = self._cache.to_absolute(rel)  # 抛 ValueError 即中止
            self._state.tab_state.tab2.track_audio_file_path = dict(track_files_rel)
            self._state.tab_state.tab2.separation_state = "done"
            self._mark_dirty()
            self.save()
            self._emit(
                "separation_done",
                {"tracks": sorted(track_files_rel.keys())},
            )

    def fail_separation(self, error: str) -> None:
        """分离失败：标 failed + emit。"""
        with self._lock:
            self._state.tab_state.tab2.separation_state = "failed"
            self._mark_dirty()
            self.save()
            self._emit(
                "separation_failed", {"model": self._state.tab_state.tab2.separation_model_name, "error": error}
            )

    def get_separation_state(self) -> RunState:
        return self._state.tab_state.tab2.separation_state

    def get_track_audio_paths(self) -> dict[str, Path]:
        """所有分轨的绝对路径。"""
        result: dict[str, Path] = {}
        for name, rel in self._state.tab_state.tab2.track_audio_file_path.items():
            try:
                result[name] = self._cache.to_absolute(rel)
            except ValueError as e:
                logger.warning("TrackAudioFilePath[%s] 不合法: %s", name, e)
        return result

    # ------------------------------------------------------------------
    # Tab3 — 分析
    # ------------------------------------------------------------------

    def upsert_analysis_task(self, track_name: str, tool_name: str) -> str:
        """新增 / 获取一个分析任务，返回 task_id。

        同 ``track_name + tool_name`` 会复用已有的首个 task_id；否则新建。
        """
        if not isinstance(track_name, str) or not track_name:
            raise ValidationError("track_name 必须是非空字符串")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValidationError("tool_name 必须是非空字符串")
        with self._lock:
            # 查找现有 task
            for key, state in self._state.tab_state.tab3.items():
                if (
                    state.analysis_tool_name == tool_name
                    and parse_tab3_key(key)[0] == track_name
                    and state.analysis_state in ("running", "done")
                ):
                    return parse_tab3_key(key)[1]
            task_id = new_task_id()
            key = tab3_key(track_name, task_id)
            if key in self._state.tab_state.tab3:
                raise TaskIdCollisionError(f"task_id 冲突: {task_id}")
            self._state.tab_state.tab3[key] = Tab3TrackState(
                analysis_tool_name=tool_name,
                analysis_state="running",
                analysis_result_path=None,
                analysis_task_id=task_id,
            )
            self._mark_dirty()
            self.save()
            self._emit(
                "analysis_started",
                {"track": track_name, "plugin": tool_name, "task_id": task_id},
            )
            return task_id

    def complete_analysis(
        self,
        track_name: str,
        task_id: str,
        result_path_rel: str,
    ) -> None:
        """分析完成：写入 result 路径（**相对** workshop_dir） + 标 done + emit。

        Args:
            result_path_rel: cache 内的相对路径。业务方在写完分析结果到
                cache/analysis_result/<plugin>_result/ 后，应自己调一次
                :py:meth:`WorkshopCache.to_relative` 再传进来。
        """
        key = tab3_key(track_name, task_id)
        if key not in self._state.tab_state.tab3:
            raise WorkshopNotFoundError(f"Tab3 task {key!r} 不存在")
        with self._lock:
            # 防御性：验证相对路径确实在 workshop_dir 内
            _ = self._cache.to_absolute(result_path_rel)  # 抛即止
            self._state.tab_state.tab3[key].analysis_state = "done"
            self._state.tab_state.tab3[key].analysis_result_path = result_path_rel
            self._mark_dirty()
            self.save()
            self._emit(
                "analysis_done",
                {
                    "track": track_name,
                    "task_id": task_id,
                    "result_path": result_path_rel,
                },
            )

    def fail_analysis(self, track_name: str, task_id: str, error: str) -> None:
        """分析失败：标 failed + emit。"""
        key = tab3_key(track_name, task_id)
        if key not in self._state.tab_state.tab3:
            raise WorkshopNotFoundError(f"Tab3 task {key!r} 不存在")
        with self._lock:
            self._state.tab_state.tab3[key].analysis_state = "failed"
            self._mark_dirty()
            self.save()
            self._emit(
                "analysis_failed",
                {"track": track_name, "task_id": task_id, "error": error},
            )

    def list_analysis_tasks(
        self,
        track_name: str | None = None,
    ) -> list[Tab3TrackState]:
        """列出 Tab3 任务；可选按 track 过滤。"""
        result: list[Tab3TrackState] = []
        for key, state in self._state.tab_state.tab3.items():
            if track_name is not None and not key.startswith(f"{track_name}::"):
                continue
            result.append(state)
        return result

    def ensure_tab4_track(self, track_name: str) -> None:
        """确保 Tab4 中存在某条 track 的状态行（调用 set_mix_state 前）。"""
        if track_name not in self._state.tab_state.tab4:
            self._state.tab_state.tab4[track_name] = Tab4TrackState()
            self._mark_dirty()

    # ------------------------------------------------------------------
    # Tab4 — 播放 / 可视化
    # ------------------------------------------------------------------

    def set_mix_state(self, track_name: str, mix: MixState) -> None:
        """Tab4 单条音轨的混音控制（volume/mute/solo）。"""
        if not isinstance(mix, MixState):
            raise ValidationError("mix 必须是 MixState 实例")
        with self._lock:
            self.ensure_tab4_track(track_name)
            self._state.tab_state.tab4[track_name].mix_state = mix
            self._mark_dirty()
            # 拖滑块高频写：用 autosave 节流，不立即落盘
            self._emit(
                "mix_state_changed",
                {"track": track_name, **mix.to_dict()},
            )

    def select_analysis_result(
        self,
        track_name: str,
        result_path: str | None,
    ) -> None:
        """Tab4 选/取消选中某条音轨的分析结果可视化。"""
        with self._lock:
            self.ensure_tab4_track(track_name)
            self._state.tab_state.tab4[
                track_name
            ].selected_analysis_result_path = result_path
            self._mark_dirty()

    def get_mix_states(self) -> dict[str, MixState]:
        """所有音轨的当前混音状态。"""
        return {
            name: state.mix_state
            for name, state in self._state.tab_state.tab4.items()
        }

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self) -> None:
        """立即原子写 state.json。"""
        with self._lock:
            try:
                self._cache.save_state(self._state.to_dict())
                self._dirty = False
            except (OSError, TypeError) as e:
                logger.error("保存 state.json 失败: %s", e)
                raise

    def to_dict(self) -> dict[str, Any]:
        """state.json 字典（HTTP API 用）。"""
        return self._state.to_dict()


# ---------------------------------------------------------------------------
# 多车间管理
# ---------------------------------------------------------------------------


class WorkshopManager:
    """进程级多车间集合。

    边界：

    * 启动时由 :py:meth:`Kernel.boot` 调 :py:meth:`load_all` 扫描磁盘
    * 给上层（HTTP）调 :py:meth:`list_workshops` / :py:meth:`create` /
      :py:meth:`switch_to` / :py:meth:`close`
    * 不直接对外暴露内部 ``dict``，强制返回 :py:class:`MusicWorkshop` 实例
    * 坏掉的 state.json **跳过**（Obsidian 风格），不影响其它车间
    """

    def __init__(
        self,
        cache_root: Path | None = None,
        event_bus: EventBus | None = None,
        autosave: bool = True,
    ) -> None:
        self._root: Path = Path(cache_root).resolve() if cache_root else Path.cwd() / "cache"
        # 确保根存在
        self._root.mkdir(parents=True, exist_ok=True)
        self._cache_mgr = CacheManager(self._root)
        self._bus = event_bus
        self._autosave = autosave
        self._workshops: dict[str, MusicWorkshop] = {}
        self._active_id: str | None = None

    # ------------------------------------------------------------------
    # 扫描与加载
    # ------------------------------------------------------------------

    def load_all(self) -> tuple[int, list[tuple[str, str]]]:
        """扫描根目录加载所有车间。

        **不**自动激活任何车间：UI 启动后应在欢迎页等用户主动选择。
        调用方在用户点选某车间时再 :py:meth:`switch_to`。

        Returns:
            ``(loaded_count, failed)`` —— 成功加载的数量与失败列表（含 ID 与原因）
        """
        ids = self._cache_mgr.list_workshop_ids()
        loaded = 0
        failed: list[tuple[str, str]] = []
        for wid in ids:
            cache = WorkshopCache(wid, root=self._root)
            try:
                raw = cache.load_state()
                if raw is None:
                    raise ValidationError("state.json 缺失或不可读")
                state = WorkshopState.from_dict(raw)
                state.validate()
                ws = MusicWorkshop(
                    wid,
                    cache,
                    state,
                    event_bus=self._bus,
                    autosave=self._autosave,
                )
                self._workshops[wid] = ws
                loaded += 1
            except (ValidationError, json.JSONDecodeError, ValueError, OSError) as e:
                logger.warning("workshop[%s] 加载失败，跳过: %s", wid, e)
                failed.append((wid, str(e)))
                if self._bus is not None:
                    self._emit(
                        "workshop_load_failed",
                        {"workshop_id": wid, "error": str(e)},
                    )
        # 不自动激活：active_id 保持 None，让 UI 决定
        return loaded, failed

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_ids(self) -> list[str]:
        """内存里所有车间 ID。"""
        return sorted(self._workshops.keys())

    def list_workshops(self) -> list[MusicWorkshop]:
        """所有车间的实例列表。"""
        return list(self._workshops.values())

    def get(self, workshop_id: str) -> MusicWorkshop | None:
        return self._workshops.get(workshop_id)

    def get_active(self) -> MusicWorkshop | None:
        if self._active_id is None:
            return None
        return self._workshops.get(self._active_id)

    def active_id(self) -> str | None:
        return self._active_id

    # ------------------------------------------------------------------
    # 创建 / 切换 / 关闭
    # ------------------------------------------------------------------

    def create(self, name: str = DEFAULT_WORKSHOP_NAME) -> MusicWorkshop:
        """创建新车间（含 state.json + 三层目录）。

        如果当前已有 active 车间，先 close 它（deactivate + 停 autosave）；
        然后把新车间设为 active 并启动它的 autosave。
        """
        wid = WorkshopCache.new_workshop_id()
        cache = WorkshopCache(wid, root=self._root)
        state = WorkshopState(workshop_name=name)
        ws = MusicWorkshop(
            wid,
            cache,
            state,
            event_bus=self._bus,
            autosave=self._autosave,
        )
        ws.save()  # 立即落盘（空 state.json）
        # 先 deactivate 任何 active 车间（UI 契约：用户操作时已 disable）
        old = self.get_active()
        if old is not None and old.id != wid:
            self.close(old.id)
        self._workshops[wid] = ws
        ws.resume_autosave()
        self._active_id = wid
        self._emit(
            "workshop_created",
            {"workshop_id": wid, "name": name},
        )
        return ws

    def switch_to(self, workshop_id: str) -> bool:
        """切换 active。

        等价于 "close 旧 + activate 新"：

        * 旧车间（如果存在）走 :py:meth:`close` 完整流程（save + 停 autosave）
        * 新车间 :py:meth:`MusicWorkshop.resume_autosave` 重新启动后台线程

        UI 调用方应在切之前 disable 旧车间的所有控件（同 close 契约）。
        """
        if workshop_id not in self._workshops:
            return False
        if self._active_id == workshop_id:
            return True
        # 关闭旧车间（如果有）
        old = self.get_active()
        if old is not None and old.id != workshop_id:
            self.close(old.id)
        # 激活新车间 + 重启 autosave
        new_ws = self._workshops[workshop_id]
        new_ws.resume_autosave()
        self._active_id = workshop_id
        self._emit("workshop_switched", {"workshop_id": workshop_id})
        return True

    def rename(self, workshop_id: str, new_name: str) -> bool:
        """重命名车间。"""
        ws = self._workshops.get(workshop_id)
        if ws is None:
            return False
        ws.rename(new_name)
        return True

    def close(self, workshop_id: str) -> bool:
        """关闭车间 = 用户回到欢迎界面（或切到别处）。

        语义约定（**UI 层契约**）：

        * 调用方应在调用本方法**之前**先在 UI 上把车间的所有控件 disable 掉
          （让用户无法再改数据），否则 autosave 已停可能丢最后一次改动。
        * MusicWorkshop 实例仍留在 :attr:`_workshops` 里（每个实例 < 2KB，无需
          pop）。列表里仍可见此车间。
        * 磁盘数据保留（state.json 已 save 一次）。
        * ``active_id`` 若是本车间，置 ``None`` → 欢迎页。

        Returns:
            是否真的关闭了（不存在返回 False）。
        """
        ws = self._workshops.get(workshop_id)
        if ws is None:
            return False
        try:
            ws.save()
        except OSError as e:
            logger.warning("关闭车间 %s 时刷盘失败: %s", workshop_id, e)
        ws.stop_autosave()
        if self._active_id == workshop_id:
            self._active_id = None
            self._emit(
                "workshop_switched", {"workshop_id": None}
            )   # payload None 表示回到欢迎页
        # 不要 pop；MusicWorkshop 仍在 _workshops（列表里仍可见）
        self._emit("workshop_closed", {"workshop_id": workshop_id})
        return True

    def delete(
        self,
        workshop_id: str,
        *,
        keep_state: bool = False,
    ) -> bool:
        """删除车间：内存清理 **+** 磁盘彻底删除。

        Args:
            keep_state: ``True`` 时把 ``state.json`` 拷到 ``recycle_bin/`` 根
                目录下 ``<id>_state.json.bak`` 后再删整间。用于"反悔"。

        Returns:
            是否真的删除了（不存在返回 False）。
        """
        ws = self._workshops.get(workshop_id)
        if ws is None and not self._cache_mgr.exists(workshop_id):
            return False
        if ws is not None:
            ws.stop_autosave()
            self._workshops.pop(workshop_id, None)
        if keep_state:
            cache = WorkshopCache(workshop_id, root=self._root)
            if cache.state_file.exists():
                recycle = self._root / "recycle_bin"
                recycle.mkdir(parents=True, exist_ok=True)
                backup = recycle / f"{workshop_id}_state.json.bak"
                try:
                    backup.write_bytes(cache.state_file.read_bytes())
                except OSError as e:
                    logger.warning("备份 state.json 失败: %s", e)
        self._cache_mgr.delete_workshop(workshop_id)
        if self._active_id == workshop_id:
            self._active_id = next(iter(self._workshops), None)
        self._emit("workshop_deleted", {"workshop_id": workshop_id})
        return True

    # ------------------------------------------------------------------
    # 关闭时统一刷盘
    # ------------------------------------------------------------------

    def save_all(self) -> None:
        """遍历所有车间立即落盘（用于优雅退出）。"""
        for ws in self._workshops.values():
            try:
                ws.save()
            except OSError as e:
                logger.error("车间 %s 退出刷盘失败: %s", ws.id, e)

    def shutdown(self) -> None:
        """关闭所有车间的 autosave 线程 + save_all。"""
        for ws in self._workshops.values():
            ws.stop_autosave()
        self.save_all()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            # WorkshopManager 不知道某个具体 workshop_id（事件是"全局"的），
            # 用空字符串占位 EventBus 的 workshop_id 位置参数
            self._bus.emit("", event_type, payload)
        except AttributeError:
            logger.debug("EventBus 接口不匹配，忽略 event=%s", event_type)


# 延迟导入避免循环依赖
from src.kernel.core.cache_system import CacheManager, WorkshopCache  # noqa: E402

__all__ = [
    "WorkshopError",
    "ValidationError",
    "WorkshopNotFoundError",
    "TaskIdCollisionError",
    "TabName",
    "RunState",
    "MixState",
    "Tab1State",
    "Tab2State",
    "Tab3TrackState",
    "Tab4TrackState",
    "TabState",
    "WorkshopState",
    "tab3_key",
    "parse_tab3_key",
    "new_task_id",
    "MusicWorkshop",
    "WorkshopManager",
]
