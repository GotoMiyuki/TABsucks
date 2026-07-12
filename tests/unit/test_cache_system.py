"""cache_system 模块的单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.kernel.core.cache_system import (
    CacheManager,
    WorkshopCache,
)


@pytest.fixture
def fresh_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """让默认 cache 根指向 tmp_path。"""
    root = tmp_path / "cache"
    monkeypatch.setenv("TABSUCKS_CACHE_DIR", str(root))
    return root


class TestWorkshopCache:
    """WorkshopCache 单车间视图。"""

    def test_init_creates_subdirectories(self, tmp_path: Path) -> None:
        """__init__ 应自动创建三层子目录。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        assert wc.workshop_dir.is_dir()
        assert wc.raw_dir.is_dir()
        assert wc.track_dir.is_dir()
        assert wc.result_dir.is_dir()

    def test_invalid_id_raises(self, tmp_path: Path) -> None:
        """非法 ID 应抛 ValueError。"""
        with pytest.raises(ValueError, match="非法 workshop_id"):
            WorkshopCache("not hex!", root=tmp_path)
        with pytest.raises(ValueError, match="非法 workshop_id"):
            WorkshopCache("", root=tmp_path)

    def test_path_helpers(self, tmp_path: Path) -> None:
        """路径生成：所有路径都应在 workshop_dir 下。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        assert wc.state_file == wc.workshop_dir / "state.json"
        track_path = wc.track_audio_path("vocals", "vocals.wav")
        assert track_path == wc.track_dir / "track_vocals" / "vocals.wav"
        meta = wc.analysis_meta_file("chord_ismir2019", "tid1234")
        result = wc.analysis_result_file("chord_ismir2019", "tid1234", "json")
        npy = wc.analysis_result_file("chord_ismir2019", "tid1234", "npy")
        assert meta.suffix == ".json"
        assert result.suffix == ".json"
        assert npy.suffix == ".npy"

    def test_to_relative_and_back(self, tmp_path: Path) -> None:
        """绝对路径 ↔ 相对路径双向转换。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        abs_path = wc.raw_dir / "song.mp3"
        rel = wc.to_relative(abs_path)
        # 跨平台：直接用 Path 对象比，不依赖分隔符字面量
        assert Path(rel) == Path("raw_audio/song.mp3")
        # 反向
        back = wc.to_absolute(rel)
        assert back == abs_path

    def test_to_absolute_rejects_traversal(self, tmp_path: Path) -> None:
        """路径遍历攻击应被拒绝。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        with pytest.raises(ValueError, match="路径含 '..'"):
            wc.to_absolute("../escape/secrets.json")

    def test_state_save_load_roundtrip(self, tmp_path: Path) -> None:
        """state.json 原子读写往返一致。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        state = {"Tab1": {"RawAudioFilePath": "raw_audio/x.wav"}, "Tab2": {}}
        wc.save_state(state)
        loaded = wc.load_state()
        assert loaded == state

    def test_load_state_missing_returns_none(self, tmp_path: Path) -> None:
        """state.json 不存在返回 None。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        # 删掉 state_file
        assert wc.load_state() is None

    def test_load_state_corrupt_returns_none(self, tmp_path: Path) -> None:
        """state.json 损坏（如半截 JSON）应返回 None，不抛。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        wc.state_file.write_text("{not valid", encoding="utf-8")
        assert wc.load_state() is None

    def test_load_state_non_dict_returns_none(self, tmp_path: Path) -> None:
        """state.json 是合法 JSON 但不是 dict，应返回 None。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        with wc.state_file.open("w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        assert wc.load_state() is None

    def test_save_raw_audio(self, tmp_path: Path) -> None:
        """save_raw_audio 复制文件并返回绝对路径。"""
        # 准备源文件
        src = tmp_path / "source.wav"
        src.write_bytes(b"fake wav data")
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        dst = wc.save_raw_audio(src, dst_filename="song.mp3")
        assert dst.exists()
        assert dst.read_bytes() == b"fake wav data"
        assert dst.name == "song.mp3"

    def test_save_raw_audio_missing_src_raises(self, tmp_path: Path) -> None:
        """源文件不存在应抛 FileNotFoundError。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        with pytest.raises(FileNotFoundError):
            wc.save_raw_audio(tmp_path / "no_such_file.wav")

    def test_save_analysis_result_dict(self, tmp_path: Path) -> None:
        """dict → .json。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        result = wc.save_analysis_result(
            "chord_ismir2019",
            "tid1234",
            {"chords": [{"root": "C", "quality": "maj"}]},
        )
        assert result.suffix == ".json"
        assert json.loads(result.read_text(encoding="utf-8")) == {
            "chords": [{"root": "C", "quality": "maj"}]
        }

    def test_save_analysis_result_ndarray(self, tmp_path: Path) -> None:
        """np.ndarray → .npy。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = wc.save_analysis_result("rhythm", "tid", arr, ext="npy")
        assert result.suffix == ".npy"
        np.testing.assert_array_equal(np.load(result), arr)

    def test_save_analysis_result_custom_ext(self, tmp_path: Path) -> None:
        """ext 任意：例如 plugin 名 happy_ext 输出 .happyeat 之类的扩展名。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        result = wc.save_analysis_result(
            "weird_plugin", "tid",
            {"x": 1},
            ext="happyeat",
        )
        assert result.suffix == ".happyeat"
        assert json.loads(result.read_text(encoding="utf-8")) == {"x": 1}

    def test_save_raw_audio_from_bytes(self, tmp_path: Path) -> None:
        """URL 流场景：bytes 直写。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        data = b"\xff\xfb\x90\x44" + b"\x00" * 100   # 假 mp3
        dst = wc.save_raw_audio_from_bytes(data, "youtube_dl.mp3")
        assert dst.exists()
        assert dst.read_bytes() == data

    def test_save_raw_audio_from_bytes_invalid(self, tmp_path: Path) -> None:
        """参数校验。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        with pytest.raises(TypeError, match="bytes"):
            wc.save_raw_audio_from_bytes("not bytes", "x.mp3")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="分隔符"):
            wc.save_raw_audio_from_bytes(b"x", "../escape.mp3")

    def test_save_analysis_result_invalid_type(self, tmp_path: Path) -> None:
        """非 dict / 非 ndarray 应抛 TypeError。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        with pytest.raises(TypeError, match="result 类型不支持"):
            wc.save_analysis_result("p", "t", "a string")  # type: ignore[arg-type]

    def test_list_tracks_empty(self, tmp_path: Path) -> None:
        """没有 track 时 list_tracks 返回空。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        assert wc.list_tracks() == []

    def test_list_tracks_after_save(self, tmp_path: Path) -> None:
        """save_track_audio 后 list_tracks 能列出来。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        src1 = tmp_path / "v.wav"
        src1.write_bytes(b"v")
        wc.save_track_audio("vocals", src1)
        src2 = tmp_path / "d.wav"
        src2.write_bytes(b"d")
        wc.save_track_audio("drums", src2)
        assert set(wc.list_tracks()) == {"vocals", "drums"}

    def test_list_tasks_dedup_meta_and_result(self, tmp_path: Path) -> None:
        """list_tasks 同时扫 meta_ 和 result_ 文件名去重。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        wc.save_analysis_meta("p1", "tA", {"x": 1})
        wc.save_analysis_result("p1", "tA", {"y": 2})
        wc.save_analysis_meta("p1", "tB", {"x": 3})
        assert set(wc.list_tasks("p1")) == {"tA", "tB"}
        assert wc.list_tasks("nonexistent") == []

    def test_delete_track_removes_dir(self, tmp_path: Path) -> None:
        """delete_track 应删除目录，不存在时静默。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        src = tmp_path / "v.wav"
        src.write_bytes(b"x")
        wc.save_track_audio("vocals", src)
        track_dir = wc.track_audio_dir("vocals")
        assert track_dir.exists()
        wc.delete_track("vocals")
        assert not track_dir.exists()
        # 再删不报错
        wc.delete_track("vocals")

    def test_delete_analysis_specific_task(self, tmp_path: Path) -> None:
        """delete_analysis(task_id) 只删对应 meta+result 文件。"""
        wid = WorkshopCache.new_workshop_id()
        wc = WorkshopCache(wid, root=tmp_path)
        wc.save_analysis_meta("p", "tA", {})
        wc.save_analysis_meta("p", "tB", {})
        wc.save_analysis_result("p", "tA", {"k": 1})
        wc.save_analysis_result("p", "tB", {"k": 2})
        wc.delete_analysis("p", "tA")
        assert "tA" not in wc.list_tasks("p")
        assert "tB" in wc.list_tasks("p")

    def test_new_workshop_id_unique(self) -> None:
        """new_workshop_id 应该返回唯一 16 位 hex。"""
        ids = {WorkshopCache.new_workshop_id() for _ in range(20)}
        assert len(ids) == 20
        for i in ids:
            assert len(i) == 16
            assert all(c in "0123456789abcdef" for c in i)


class TestCacheManager:
    """CacheManager 顶层扫描。"""

    def test_list_empty(self, tmp_path: Path) -> None:
        cm = CacheManager(root=tmp_path)
        assert cm.list_workshop_ids() == []

    def test_list_with_workshops(self, tmp_path: Path) -> None:
        """扫描根目录下所有 workshop_<id>。"""
        for wid in ["aabb0011", "ccdd0022", "no_prefix"]:
            (tmp_path / f"workshop_{wid}").mkdir()
        cm = CacheManager(root=tmp_path)
        ids = cm.list_workshop_ids()
        assert set(ids) == {"aabb0011", "ccdd0022"}

    def test_list_on_missing_root(self, tmp_path: Path) -> None:
        """根目录不存在应返回空，不抛。"""
        cm = CacheManager(root=tmp_path / "not_exist")
        assert cm.list_workshop_ids() == []

    def test_exists(self, tmp_path: Path) -> None:
        cm = CacheManager(root=tmp_path)
        WorkshopCache("aabb0011", root=tmp_path)
        assert cm.exists("aabb0011")
        assert not cm.exists("ffffffff")

    def test_delete_workshop(self, tmp_path: Path) -> None:
        """delete_workshop 应删整间车间；不存在返回 False。"""
        wc = WorkshopCache("aabb0011", root=tmp_path)
        assert wc.workshop_dir.exists()
        cm = CacheManager(root=tmp_path)
        assert cm.delete_workshop("aabb0011") is True
        assert not wc.workshop_dir.exists()
        assert cm.delete_workshop("aabb0011") is False

    def test_delete_workshop_with_content(self, tmp_path: Path) -> None:
        """删除含 state.json 的车间也能成功。"""
        wc = WorkshopCache("aabb0011", root=tmp_path)
        wc.save_state({"a": 1})
        cm = CacheManager(root=tmp_path)
        assert cm.delete_workshop("aabb0011") is True


# ---------------------------------------------------------------------------
# 烟雾测试：环境变量影响默认根目录
# ---------------------------------------------------------------------------


def test_env_var_overrides_default_root(fresh_cache_root: Path) -> None:
    """``TABSUCKS_CACHE_DIR`` 环境变量被识别为默认根。"""
    # 触发一次常量解析
    import importlib

    from src.kernel.core import cache_system

    importlib.reload(cache_system)
    assert cache_system.CACHE_ROOT_DEFAULT == fresh_cache_root
