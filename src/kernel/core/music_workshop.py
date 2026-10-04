"""Single-workshop state transitions and autosave lifecycle."""

from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path
from typing import Any

from .cache_system import WorkshopCache
from .event_bus import EventBus
from .workshop_state import (
    DEFAULT_WORKSHOP_NAME,
    TRACK_NAMES,
    MixState,
    RunState,
    Tab2State,
    Tab3TrackState,
    Tab4TrackState,
    TabName,
    TaskIdCollisionError,
    ValidationError,
    WorkshopNotFoundError,
    WorkshopState,
    new_task_id,
    parse_tab3_key,
    tab3_key,
)

logger = logging.getLogger(__name__)


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
                        logger.warning("workshop[%s] autosave 失败: %s", self.id, e)

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
        *,
        display_filename: str | None = None,
        task_id: str | None = None,
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
            previous = copy.deepcopy(self._state)
            try:
                self._state.tab_state.tab1.raw_audio_file_path = rel
                self._invalidate_analysis_for_new_input()
                self._mark_dirty()
                self._maybe_auto_name(display_filename or Path(rel).name)
                self.save()
            except Exception:
                self._state = previous
                raise
            payload = {"path": rel}
            if task_id is not None:
                payload["task_id"] = task_id
            self._emit("raw_audio_set", payload)
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
            previous = copy.deepcopy(self._state)
            try:
                self._state.tab_state.tab1.raw_audio_file_path = rel
                self._invalidate_analysis_for_new_input()
                self._mark_dirty()
                self._maybe_auto_name(Path(rel).name)
                self.save()
            except Exception:
                self._state = previous
                raise
            self._emit("raw_audio_set", {"path": rel})
            return abs_path

    def _invalidate_analysis_for_new_input(self) -> None:
        """A new source makes the old stems and analyses unusable."""
        self._state.tab_state.tab2 = Tab2State()
        self._state.tab_state.tab3.clear()
        self._state.tab_state.tab4.clear()

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
        task_id: str | None = None,
    ) -> None:
        """分离开始：标 running + emit。"""
        with self._lock:
            previous = copy.deepcopy(self._state)
            self._state.tab_state.tab2.separation_state = "running"
            self._state.tab_state.tab2.separation_model_name = model_name
            self._state.tab_state.tab2.separation_model_path = model_path
            try:
                self._mark_dirty()
                self.save()
            except Exception:
                self._state = previous
                raise
            payload = {"model": model_name}
            if task_id is not None:
                payload["task_id"] = task_id
            self._emit("separation_started", payload)

    def complete_separation(
        self,
        track_files_rel: dict[str, str],
        task_id: str | None = None,
    ) -> None:
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
                path = self._cache.to_absolute(rel)
                if not path.is_file():
                    raise FileNotFoundError(path)
            previous = copy.deepcopy(self._state)
            try:
                self._state.tab_state.tab2.track_audio_file_path = dict(track_files_rel)
                self._state.tab_state.tab2.selected_tracks = []
                self._state.tab_state.tab2.separation_state = "done"
                self._state.tab_state.tab3.clear()
                self._state.tab_state.tab4.clear()
                self._mark_dirty()
                self.save()
            except Exception:
                self._state = previous
                raise
            payload = {"tracks": sorted(track_files_rel.keys())}
            if task_id is not None:
                payload["task_id"] = task_id
            self._emit("separation_done", payload)

    def fail_separation(self, error: str, task_id: str | None = None) -> None:
        """分离失败：标 failed + emit。"""
        with self._lock:
            self._state.tab_state.tab2.separation_state = "failed"
            self._mark_dirty()
            self.save()
            payload = {"model": self._state.tab_state.tab2.separation_model_name, "error": error}
            if task_id is not None:
                payload["task_id"] = task_id
            self._emit("separation_failed", payload)

    def cancel_separation(self, *, task_id: str | None = None) -> None:
        with self._lock:
            self._state.tab_state.tab2.separation_state = "cancelled"
            self.save()
            self._emit("separation_cancelled", {"task_id": task_id})

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

    def set_selected_tracks(self, track_names: list[str]) -> None:
        """保存 Tab2 用户选择的下游分析音轨，按固定六轨顺序去重。"""
        if not isinstance(track_names, list) or any(
            not isinstance(track, str) or track not in TRACK_NAMES for track in track_names
        ):
            raise ValidationError("SelectedTracks 包含非法音轨名")
        with self._lock:
            tab2 = self._state.tab_state.tab2
            if tab2.separation_state != "done":
                raise ValidationError("音轨分离尚未完成，不能保存 SelectedTracks")
            available = set(tab2.track_audio_file_path)
            unavailable = [track for track in track_names if track not in available]
            if unavailable:
                raise ValidationError(f"SelectedTracks 包含不可用音轨: {unavailable}")
            selected = [track for track in TRACK_NAMES if track in track_names]
            self._state.tab_state.tab2.selected_tracks = selected
            self._mark_dirty()
            self.save()
            self._emit("selected_tracks_changed", {"tracks": selected})

    def get_selected_tracks(self) -> list[str]:
        """返回 Tab2 已选择的音轨副本。"""
        return list(self._state.tab_state.tab2.selected_tracks)

    # ------------------------------------------------------------------
    # Tab3 — 分析
    # ------------------------------------------------------------------

    def upsert_analysis_task(
        self,
        track_name: str,
        tool_name: str,
        task_id: str | None = None,
    ) -> str:
        """新增 / 获取一个分析任务，返回 task_id。

        同 ``track_name + tool_name`` 仅复用仍在运行的任务；已完成或失败后的重跑
        会创建新 task_id，确保最新结果在持久化顺序中可被可靠恢复。
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
                    and state.analysis_state == "running"
                    and (task_id is None or parse_tab3_key(key)[1] == task_id)
                ):
                    return parse_tab3_key(key)[1]
            task_id = task_id or new_task_id()
            key = tab3_key(track_name, task_id)
            if key in self._state.tab_state.tab3:
                raise TaskIdCollisionError(f"task_id 冲突: {task_id}")
            self._state.tab_state.tab3[key] = Tab3TrackState(
                analysis_tool_name=tool_name,
                analysis_state="running",
                analysis_result_path=None,
                analysis_task_id=task_id,
            )
            try:
                self._mark_dirty()
                self.save()
            except Exception:
                self._state.tab_state.tab3.pop(key, None)
                raise
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
        *,
        result: dict[str, Any] | None = None,
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
            previous = copy.deepcopy(self._state)
            try:
                self._state.tab_state.tab3[key].analysis_state = "done"
                self._state.tab_state.tab3[key].analysis_result_path = result_path_rel
                self._mark_dirty()
                self.save()
            except Exception:
                self._state = previous
                raise
            payload: dict[str, Any] = {
                "track": track_name,
                "plugin": self._state.tab_state.tab3[key].analysis_tool_name,
                "task_id": task_id,
                "result_path": result_path_rel,
            }
            if result is not None:
                payload["result"] = result
            self._emit("analysis_done", payload)

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
                {
                    "track": track_name,
                    "plugin": self._state.tab_state.tab3[key].analysis_tool_name,
                    "task_id": task_id,
                    "error": error,
                },
            )

    def cancel_analysis(self, track_name: str, task_id: str) -> None:
        key = tab3_key(track_name, task_id)
        with self._lock:
            state = self._state.tab_state.tab3.get(key)
            if state is None:
                return
            state.analysis_state = "cancelled"
            self.save()
            self._emit(
                "analysis_cancelled",
                {"track": track_name, "plugin": state.analysis_tool_name, "task_id": task_id},
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
            self._state.tab_state.tab4[track_name].selected_analysis_result_path = result_path
            self._mark_dirty()

    def get_mix_states(self) -> dict[str, MixState]:
        """所有音轨的当前混音状态。"""
        return {name: state.mix_state for name, state in self._state.tab_state.tab4.items()}

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
