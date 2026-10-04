"""Process-level workshop collection, recovery and lifecycle."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .cache_system import CacheManager, WorkshopCache
from .event_bus import EventBus
from .music_workshop import MusicWorkshop
from .state_migrations import CURRENT_SCHEMA_VERSION
from .workshop_state import DEFAULT_WORKSHOP_NAME, ValidationError, WorkshopState

logger = logging.getLogger(__name__)


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
                # Process-local execution handles cannot survive a restart.
                interrupted = False
                if state.tab_state.tab2.separation_state == "running":
                    state.tab_state.tab2.separation_state = "interrupted"
                    interrupted = True
                for analysis_state in state.tab_state.tab3.values():
                    if analysis_state.analysis_state == "running":
                        analysis_state.analysis_state = "interrupted"
                        interrupted = True
                needs_migration = raw.get("SchemaVersion", 0) < CURRENT_SCHEMA_VERSION
                if needs_migration:
                    # Exclusive creation preserves the original on repeated attempts.
                    backup = cache.state_file.with_name("state.pre-v1.json.bak")
                    original = cache.state_file.read_bytes()
                    if not backup.exists():
                        with backup.open("xb") as target:
                            target.write(original)
                    if backup.read_bytes() != original:
                        raise ValidationError(
                            "迁移备份与原文件不一致，保留 state.json 等待人工确认"
                        )
                if interrupted or needs_migration:
                    cache.save_state(state.to_dict())
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
            self._emit("workshop_switched", {"workshop_id": None})  # payload None 表示回到欢迎页
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
