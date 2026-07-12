"""车间缓存与文件系统 IO 模块。

设计原则（来自 ``docs/meetings/2026-6-16-meeting.md`` §1.2）：

* 一间车间一个目录：``cache/workshop_<id>/``
* 目录树：

  ::

      workshop_<id>/
          state.json
          raw_audio/<file>
          track_audio/track_<name>/<file>
          analysis_result/<plugin>_result/
              meta_<task_id>.json
              result_<task_id>.<ext>

* state.json 里 **只存相对路径**（以 ``workshop_<id>/`` 为基准），跨设备
  同步整个 ``cache/`` 目录不会失效。
* 运行时全部用绝对路径 IO，转换由 :py:meth:`WorkshopCache.to_relative` /
  :py:meth:`WorkshopCache.to_absolute` 完成。
* 写文件一律临时 + rename，掉电不产生半截文件。
* 本模块 **不依赖** :py:mod:`src.kernel.core.workshop` —— 纯 IO 边界，业务
  层禁止反向 ``import``。

边界：本模块不知道 ``MusicWorkshop`` / ``WorkshopState``，只接受 plain
``dict`` 或 list/tuple/ndarray 三种序列化形式（dict → .json，ndarray →
.npy）。
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

import numpy as np

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 缓存根目录，允许通过 ``TABSUCKS_CACHE_DIR`` 环境变量覆盖。
CACHE_ROOT_DEFAULT: Path = Path(
    os.environ.get("TABSUCKS_CACHE_DIR", "cache")
).resolve()

#: 车间目录前缀（避免和大文件/目录冲突）
WORKSHOP_DIR_PREFIX: str = "workshop_"

#: 支持的分析结果格式（识别 .npy 还是 .json 后缀用）
RESULT_FORMAT_JSON: str = "json"
RESULT_FORMAT_NPY: str = "npy"
ResultFormat = Literal["json", "npy"]

#: 车间 ID 长度（uuid4 hex 截断，16 hex 足以唯一）
WORKSHOP_ID_LENGTH: int = 16


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class CacheSystemError(Exception):
    """缓存文件系统错误。"""


class WorkshopDirMissingError(CacheSystemError):
    """车间目录不存在。"""


# ---------------------------------------------------------------------------
# 单车间缓存视图
# ---------------------------------------------------------------------------


class WorkshopCache:
    """单一车间的目录树 + 文件级 IO。

    不持有任何业务对象，只负责路径计算与原子写入。
    """

    def __init__(
        self,
        workshop_id: str,
        root: Path | None = None,
    ) -> None:
        """初始化并创建三层子目录。

        Args:
            workshop_id: 车间唯一 ID（hex 字符串，推荐 16 位）。
            root: 缓存根目录；``None`` 时用 :data:`CACHE_ROOT_DEFAULT`。

        Raises:
            ValueError: ``workshop_id`` 含非法字符。
            CacheSystemError: 目录创建失败。
        """
        if not workshop_id or not all(
            c in "0123456789abcdef-" for c in workshop_id
        ):
            raise ValueError(
                f"非法 workshop_id: {workshop_id!r}（仅允许 hex / -）"
            )
        self.workshop_id: str = workshop_id
        self.root: Path = (root or CACHE_ROOT_DEFAULT).resolve()
        self.workshop_dir: Path = self.root / f"{WORKSHOP_DIR_PREFIX}{workshop_id}"
        self.raw_dir: Path = self.workshop_dir / "raw_audio"
        self.track_dir: Path = self.workshop_dir / "track_audio"
        self.result_dir: Path = self.workshop_dir / "analysis_result"
        try:
            for d in (self.raw_dir, self.track_dir, self.result_dir):
                d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise CacheSystemError(
                f"创建车间目录失败: {self.workshop_dir}"
            ) from e

    # ---- 路径生成（不创建）----

    @property
    def state_file(self) -> Path:
        """``state.json`` 的绝对路径。"""
        return self.workshop_dir / "state.json"

    def raw_audio_path(self, filename: str) -> Path:
        """原始音频落盘路径，``filename`` 通常取 ``Path(src).name``。"""
        return self.raw_dir / filename

    def track_audio_dir(self, track_name: str) -> Path:
        """某条分轨音频的目录路径。"""
        return self.track_dir / f"track_{track_name}"

    def track_audio_path(self, track_name: str, filename: str) -> Path:
        """某条分轨音频的文件路径。"""
        return self.track_audio_dir(track_name) / filename

    def analysis_plugin_dir(self, plugin_name: str) -> Path:
        """某个分析插件的结果目录。"""
        return self.result_dir / f"{plugin_name}_result"

    def analysis_meta_file(self, plugin_name: str, task_id: str) -> Path:
        """分析 meta 文件路径（``meta_<task_id>.json``）。"""
        return self.analysis_plugin_dir(plugin_name) / f"meta_{task_id}.json"

    def analysis_result_file(
        self,
        plugin_name: str,
        task_id: str,
        ext: str = RESULT_FORMAT_JSON,
    ) -> Path:
        """分析结果文件路径。

        ``ext`` 由调用方（通常是 plugin）决定：``.json`` / ``.npy`` / 任意。
        例：plugin 名 ``madmom_rhythm`` 输出 numpy array → ``ext="npy"``；
        plugin 名 ``chord_ismir2019`` 输出 dict → ``ext="json"``。
        """
        return self.analysis_plugin_dir(plugin_name) / f"result_{task_id}.{ext}"

    # ---- 相对路径转换 ----

    def to_relative(self, abs_path: Path | str) -> str:
        """绝对路径 → 相对 ``workshop_dir`` 的字符串（写 state.json 用）。

        Raises:
            ValueError: 路径不在该车间目录内。
        """
        p = Path(abs_path).resolve()
        try:
            return str(p.relative_to(self.workshop_dir))
        except ValueError as e:
            raise ValueError(
                f"路径 {p} 不在车间目录 {self.workshop_dir} 内"
            ) from e

    def to_absolute(self, rel_path: str) -> Path:
        """state.json 里读出的相对路径 → 绝对路径。

        安全：拒绝任何含 ``..`` 的相对路径（防止路径遍历攻击）。
        """
        if not rel_path:
            raise ValueError("rel_path 不能为空")
        if ".." in Path(rel_path).parts:
            raise ValueError(f"路径含 '..'，疑似遍历攻击: {rel_path!r}")
        return (self.workshop_dir / rel_path).resolve()

    # ---- 状态文件读写（原子）----

    def save_state(self, state: dict[str, Any]) -> None:
        """原子写入 ``state.json``：先写临时文件，再 ``rename``。"""
        tmp = self.state_file.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_file)
        except OSError:
            # 清理临时文件，不留垃圾
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

    def load_state(self) -> dict[str, Any] | None:
        """加载 ``state.json``；不存在或损坏返回 ``None``（让上层自行决定）。"""
        if not self.state_file.exists():
            return None
        try:
            with self.state_file.open("r", encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        return result

    # ---- 原始 / 分轨音频入库 ----

    def save_raw_audio(
        self,
        src_path: Path | str,
        dst_filename: str | None = None,
    ) -> Path:
        """复制 ``src_path`` 到 ``raw_audio/``，返回落盘后的绝对路径。

        适用于"本地上传"路径：用户已有一个文件，复制进 cache 以便
        workshop 唯一持有（避免用户的删除反向影响 cache）。

        URL 下载场景请用 :py:meth:`save_raw_audio_from_bytes`。
        """
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError(f"原始音频不存在: {src}")
        dst_name = dst_filename or src.name
        dst = self.raw_audio_path(dst_name)
        shutil.copy2(src, dst)
        return dst

    def save_raw_audio_from_bytes(
        self,
        data: bytes,
        dst_filename: str,
    ) -> Path:
        """直接落盘字节流到 ``raw_audio/<dst_filename>``。

        适用于"URL 下载"路径：调用方已经下载到内存（``load_audio_from_url``
        返回 AudioData 后**被 load_audio 删除临时文件**，cache 没路径可复制）；
        网络层通常会再请求一次（或在 download_audio_from_url 流程里
        把 yt-dlp 输出文件指针拿到后读 bytes），然后本方法直接 write_bytes。

        Args:
            data: 文件字节内容。
            dst_filename: 落盘文件名（如 ``"song.mp3"``）。

        Returns:
            落盘后的绝对路径。
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(f"data 必须是 bytes，得到 {type(data).__name__}")
        if not dst_filename or "/" in dst_filename or "\\" in dst_filename:
            raise ValueError(f"dst_filename 不能含路径分隔符: {dst_filename!r}")
        dst = self.raw_audio_path(dst_filename)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(bytes(data))
        return dst

    def save_track_audio(
        self,
        track_name: str,
        src_path: Path | str,
        dst_filename: str | None = None,
    ) -> Path:
        """复制分轨文件到 ``track_audio/track_<name>/``。"""
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError(f"分轨文件不存在: {src}")
        track_dir = self.track_audio_dir(track_name)
        track_dir.mkdir(parents=True, exist_ok=True)
        dst_name = dst_filename or src.name
        dst = track_dir / dst_name
        shutil.copy2(src, dst)
        return dst

    # ---- 分析结果入库 ----

    def save_analysis_meta(
        self,
        plugin_name: str,
        task_id: str,
        meta: dict[str, Any],
    ) -> Path:
        """写入 ``meta_<task_id>.json``（原子）。"""
        plugin_dir = self.analysis_plugin_dir(plugin_name)
        plugin_dir.mkdir(parents=True, exist_ok=True)
        meta_path = self.analysis_meta_file(plugin_name, task_id)
        tmp = meta_path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(tmp, meta_path)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
        return meta_path

    def save_analysis_result(
        self,
        plugin_name: str,
        task_id: str,
        result: dict[str, Any] | np.ndarray,
        ext: str = RESULT_FORMAT_JSON,
    ) -> Path:
        """写入 ``result_<task_id>.<ext>``。

        * ``dict`` → 写为 ``.json``（无论 ``ext`` 是什么）
        * ``np.ndarray`` → 写为 ``.npy``（无论 ``ext`` 是什么）

        当 result 不是这两种类型时，**调用方应自行落盘并把路径放进 state.json**。
        例如：分离模型输出多 wav → 业务方调 :py:meth:`save_track_audio` 即可，
        然后用 ``state.json`` 记相对路径。

        Args:
            plugin_name: 插件名，决定子目录。
            task_id: 任务 ID。
            result: dict 或 ndarray。
            ext: 文件扩展名（无点）。约定由 plugin 自报，但本方法本身
                不用 ext 区分 dict/ndarray（dict→.json，ndarray→.npy）。
        """
        plugin_dir = self.analysis_plugin_dir(plugin_name)
        plugin_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(result, np.ndarray):
            out_path = self.analysis_result_file(
                plugin_name, task_id, ext=ext
            )
            np.save(out_path, result)
            return out_path

        if isinstance(result, dict):
            out_path = self.analysis_result_file(
                plugin_name, task_id, ext=ext
            )
            tmp = out_path.with_suffix(".json.tmp")
            try:
                with tmp.open("w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                os.replace(tmp, out_path)
            except OSError:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                raise
            return out_path

        raise TypeError(
            f"result 类型不支持: {type(result).__name__}（仅 dict / np.ndarray；"
            "其它类型由调用方自行落盘 + 写入 state.json 路径）"
        )

    # ---- 列出与删除 ----

    def list_tracks(self) -> list[str]:
        """扫描 ``track_audio/``，返回所有 ``track_<name>`` 目录下的 ``name``。

        返回的 name 不含 ``track_`` 前缀，便于业务层直接使用。
        """
        if not self.track_dir.exists():
            return []
        names: list[str] = []
        for entry in sorted(self.track_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("track_"):
                names.append(entry.name[len("track_"):])
        return names

    def list_plugins(self) -> list[str]:
        """扫描 ``analysis_result/``，返回所有 ``<plugin>_result`` 目录下的 plugin 名。"""
        if not self.result_dir.exists():
            return []
        names: list[str] = []
        for entry in sorted(self.result_dir.iterdir()):
            if entry.is_dir() and entry.name.endswith("_result"):
                names.append(entry.name[: -len("_result")])
        return names

    def list_tasks(self, plugin_name: str) -> list[str]:
        """扫描 ``<plugin>_result/``，返回所有 ``task_id``。

        ``task_id`` 同时从 ``meta_`` 与 ``result_`` 前缀文件名收集，去重并排序。
        """
        plugin_dir = self.analysis_plugin_dir(plugin_name)
        if not plugin_dir.exists():
            return []
        tasks: set[str] = set()
        for entry in sorted(plugin_dir.iterdir()):
            if not entry.is_file():
                continue
            stem = entry.stem
            if stem.startswith("meta_"):
                tasks.add(stem[len("meta_"):])
            elif stem.startswith("result_"):
                tasks.add(stem[len("result_"):])
        return sorted(tasks)

    def delete_track(self, track_name: str) -> None:
        """删除整条分轨目录；不存在则静默忽略。"""
        d = self.track_audio_dir(track_name)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def delete_analysis(
        self,
        plugin_name: str,
        task_id: str | None = None,
    ) -> None:
        """删除分析结果。

        * ``task_id=None`` → 删除整个 ``<plugin>_result/`` 目录
        * 指定 ``task_id`` → 只删对应的 ``meta_*`` 与 ``result_*`` 文件
        """
        plugin_dir = self.analysis_plugin_dir(plugin_name)
        if not plugin_dir.exists():
            return
        if task_id is None:
            shutil.rmtree(plugin_dir, ignore_errors=True)
            return
        for prefix in ("meta_", "result_"):
            for suffix in (".json", ".npy"):
                f = plugin_dir / f"{prefix}{task_id}{suffix}"
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass

    # ---- 工具 ----

    @staticmethod
    def new_workshop_id() -> str:
        """生成新的 16 位 hex 车间 ID。"""
        return uuid.uuid4().hex[:WORKSHOP_ID_LENGTH]


# ---------------------------------------------------------------------------
# 顶层缓存管理器（扫描根目录）
# ---------------------------------------------------------------------------


class CacheManager:
    """扫描缓存根目录，返回车间 ID 列表。

    不读 state.json，真正加载由
    :py:class:`src.kernel.core.workshop.WorkshopManager.load_all` 完成。
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root: Path = (root or CACHE_ROOT_DEFAULT).resolve()

    def list_workshop_ids(self) -> list[str]:
        """返回根目录下所有 ``workshop_<id>`` 子目录的 ID（不含前缀）。

        目录名格式校验失败（如 ``workshop_`` 后带非法字符）会跳过。
        """
        if not self.root.exists():
            return []
        ids: list[str] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            if not entry.name.startswith(WORKSHOP_DIR_PREFIX):
                continue
            wid = entry.name[len(WORKSHOP_DIR_PREFIX):]
            if wid and all(c in "0123456789abcdef-" for c in wid):
                ids.append(wid)
        return ids

    def exists(self, workshop_id: str) -> bool:
        """车间目录是否存在。"""
        return (self.root / f"{WORKSHOP_DIR_PREFIX}{workshop_id}").is_dir()

    def delete_workshop(self, workshop_id: str) -> bool:
        """删除整间车间（包括 state.json + 全部子目录）。

        Returns:
            ``True`` 表示删了；``False`` 表示本来就不存在。
        """
        d = self.root / f"{WORKSHOP_DIR_PREFIX}{workshop_id}"
        if not d.exists():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True


__all__ = [
    "CACHE_ROOT_DEFAULT",
    "WORKSHOP_DIR_PREFIX",
    "RESULT_FORMAT_JSON",
    "RESULT_FORMAT_NPY",
    "ResultFormat",
    "CacheSystemError",
    "WorkshopDirMissingError",
    "WorkshopCache",
    "CacheManager",
]
