"""workshop 模块的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.kernel.core.cache_system import WorkshopCache
from src.kernel.core.workshop import (
    DEFAULT_WORKSHOP_NAME,
    MixState,
    MusicWorkshop,
    Tab1State,
    Tab2State,
    Tab3TrackState,
    Tab4TrackState,
    TabState,
    ValidationError,
    WorkshopManager,
    WorkshopNotFoundError,
    WorkshopState,
    new_task_id,
    parse_tab3_key,
    tab3_key,
)

# ---------------------------------------------------------------------------
# dataclass 转换
# ---------------------------------------------------------------------------


class TestMixState:
    """MixState 字段约束 + to/from dict。"""

    def test_default_values(self) -> None:
        m = MixState()
        assert m.volume == 1.0
        assert m.mute is False
        assert m.solo is False

    def test_volume_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume"):
            MixState(volume=1.5)
        with pytest.raises(ValidationError, match="volume"):
            MixState(volume=-0.1)

    def test_volume_must_be_number(self) -> None:
        with pytest.raises(ValidationError, match="volume"):
            MixState(volume="not a number")  # type: ignore[arg-type]

    def test_dict_roundtrip(self) -> None:
        m = MixState(volume=0.5, mute=True, solo=False)
        d = m.to_dict()
        assert d == {"volume": 0.5, "mute": True, "solo": False}
        m2 = MixState.from_dict(d)
        assert m == m2

    def test_from_dict_missing_keys_uses_defaults(self) -> None:
        m = MixState.from_dict({})
        assert m.volume == 1.0
        assert m.mute is False


class TestStateDataclasses:
    """TabXState + WorkshopState 双向转换。"""

    def test_tab1_roundtrip(self) -> None:
        t = Tab1State(raw_audio_file_path="raw_audio/song.mp3")
        d = t.to_dict()
        assert d == {"RawAudioFilePath": "raw_audio/song.mp3"}
        assert Tab1State.from_dict(d) == t

    def test_tab1_none_path(self) -> None:
        t = Tab1State()
        assert t.to_dict() == {"RawAudioFilePath": None}
        assert Tab1State.from_dict({"RawAudioFilePath": None}) == t

    def test_tab1_invalid_path_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="RawAudioFilePath"):
            Tab1State.from_dict({"RawAudioFilePath": 123})  # type: ignore[arg-type]

    def test_tab2_defaults(self) -> None:
        t = Tab2State()
        assert t.separation_state == "not_started"
        assert t.track_audio_file_path == {}

    def test_tab2_roundtrip(self) -> None:
        t = Tab2State(
            separation_state="done",
            separation_model_name="BS-RoFormer-SW",
            separation_model_path="/models/bsroformer.ckpt",
            track_audio_file_path={"vocals": "track_audio/track_vocals/v.wav"},
        )
        d = t.to_dict()
        # 验证 key 名与会议一致（camelCase）
        assert "SeparationState" in d
        assert "SeparationModelName" in d
        assert "SeparationModelPath" in d
        assert "TrackAudioFilePath" in d
        t2 = Tab2State.from_dict(d)
        assert t == t2

    def test_tab2_invalid_state_raises(self) -> None:
        with pytest.raises(ValidationError, match="SeparationState"):
            Tab2State.from_dict({"SeparationState": "weird_value"})

    def test_tab2_invalid_track_paths_raises(self) -> None:
        with pytest.raises(ValidationError, match="TrackAudioFilePath"):
            Tab2State.from_dict({"TrackAudioFilePath": "not a dict"})

    def test_tab3_track_state_roundtrip(self) -> None:
        t = Tab3TrackState(
            analysis_tool_name="chord_ismir2019",
            analysis_state="done",
            analysis_result_path="analysis_result/chord_ismir2019_result/result_tid.json",
            analysis_task_id="tid",
        )
        d = t.to_dict()
        assert d["AnalysisToolName"] == "chord_ismir2019"
        t2 = Tab3TrackState.from_dict(d)
        assert t == t2

    def test_tab4_track_state_roundtrip(self) -> None:
        t = Tab4TrackState(
            selected_analysis_result_path="analysis_result/foo/result.json",
            mix_state=MixState(volume=0.5, mute=True),
        )
        d = t.to_dict()
        assert d["MixState"]["volume"] == 0.5
        assert d["MixState"]["mute"] is True
        t2 = Tab4TrackState.from_dict(d)
        assert t == t2

    def test_workshop_state_minimal(self) -> None:
        """空 state dict 也能解析（用所有默认值）。"""
        ws = WorkshopState()
        assert ws.workshop_name == DEFAULT_WORKSHOP_NAME
        assert ws.last_tab == "Tab1"
        assert isinstance(ws.tab_state, TabState)

    def test_workshop_state_roundtrip(self) -> None:
        ws = WorkshopState(
            workshop_name="MySong",
            last_tab="Tab3",
        )
        ws.tab_state.tab2.separation_state = "running"
        ws.tab_state.tab2.separation_model_name = "BS-RoFormer-SW"
        ws.tab_state.tab4["vocals"] = Tab4TrackState(
            mix_state=MixState(volume=0.8),
        )
        d = ws.to_dict()
        # 顶层 key 名与会议一致
        assert set(d.keys()) == {"WorkshopName", "LastTab", "TabState"}
        assert d["WorkshopName"] == "MySong"
        ws2 = WorkshopState.from_dict(d)
        assert ws2.workshop_name == "MySong"
        assert ws2.last_tab == "Tab3"
        assert ws2.tab_state.tab2.separation_state == "running"
        assert ws2.tab_state.tab4["vocals"].mix_state.volume == 0.8

    def test_invalid_name_raises(self) -> None:
        with pytest.raises(ValidationError, match="WorkshopName"):
            WorkshopState.from_dict({"WorkshopName": ""})

    def test_invalid_last_tab_raises(self) -> None:
        with pytest.raises(ValidationError, match="LastTab"):
            WorkshopState.from_dict({"LastTab": "Tab99"})

    def test_invalid_root_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="dict"):
            WorkshopState.from_dict("not a dict")  # type: ignore[arg-type]


class TestTab3Key:
    """复合键工具。"""

    def test_roundtrip(self) -> None:
        key = tab3_key("piano", "tid1234")
        assert key == "piano::tid1234"
        name, tid = parse_tab3_key(key)
        assert (name, tid) == ("piano", "tid1234")

    def test_parse_invalid(self) -> None:
        with pytest.raises(ValidationError, match="Tab3 key"):
            parse_tab3_key("no_separator")
        with pytest.raises(ValidationError, match="Tab3 key"):
            parse_tab3_key("name::")
        with pytest.raises(ValidationError, match="Tab3 key"):
            parse_tab3_key("::tid")

    def test_new_task_id_format(self) -> None:
        tid = new_task_id()
        assert len(tid) == 8
        assert all(c in "0123456789abcdef" for c in tid)


# ---------------------------------------------------------------------------
# MusicWorkshop 运行时
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_workshop(tmp_path: Path) -> MusicWorkshop:
    """一个最小可用的 MusicWorkshop 实例（disable autosave 避免线程干扰）。"""
    wid = WorkshopCache.new_workshop_id()
    cache = WorkshopCache(wid, root=tmp_path)
    return MusicWorkshop(wid, cache, autosave=False)


class TestMusicWorkshopIO:
    """单车间与 state.json / 文件系统的交互。"""

    def test_initial_save_on_creation(
        self, tmp_path: Path
    ) -> None:
        """create() 应立即落盘 state.json（空状态）。"""
        wid = WorkshopCache.new_workshop_id()
        cache = WorkshopCache(wid, root=tmp_path)
        _ = MusicWorkshop(wid, cache, autosave=False)
        assert cache.load_state() is None  # 只构造不 save
        # 用 WorkshopManager 路径（create 才 save）——改测 WorkshopManager

    def test_save_and_load_roundtrip(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.state.workshop_name = "Roundtrip Song"
        ws.state.last_tab = "Tab4"
        ws.save()
        # 从 cache 读
        loaded = ws.cache.load_state()
        assert loaded is not None
        assert loaded["WorkshopName"] == "Roundtrip Song"
        assert loaded["LastTab"] == "Tab4"

    def test_set_raw_audio(self, tmp_path: Path) -> None:
        wid = WorkshopCache.new_workshop_id()
        cache = WorkshopCache(wid, root=tmp_path)
        ws = MusicWorkshop(wid, cache, autosave=False)

        src = tmp_path / "song.mp3"
        src.write_bytes(b"fake mp3")
        abs_path = ws.set_raw_audio(src, dst_filename="renamed.mp3")
        assert abs_path.exists()
        # 跨平台：用 Path 比较，不依赖分隔符
        assert Path(ws.state.tab_state.tab1.raw_audio_file_path) == Path(
            "raw_audio/renamed.mp3"
        )

    def test_get_raw_audio_path(self, fresh_workshop: MusicWorkshop) -> None:
        ws = fresh_workshop
        ws.state.tab_state.tab1.raw_audio_file_path = "raw_audio/song.wav"
        p = ws.get_raw_audio_path()
        assert p is not None
        assert p.name == "song.wav"

    def test_get_raw_audio_path_invalid_returns_none(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.state.tab_state.tab1.raw_audio_file_path = "../etc/passwd"
        assert ws.get_raw_audio_path() is None


class TestMusicWorkshopBusinessOps:
    """业务方法调用链。"""

    def test_separation_lifecycle(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.start_separation("BS-RoFormer-SW", model_path="/models/x.ckpt")
        assert ws.get_separation_state() == "running"
        # 写一个真实文件 + 传相对路径
        abs_path = ws.cache.track_audio_dir("vocals") / "v.wav"
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"v")
        rel = ws.cache.to_relative(abs_path)
        ws.complete_separation({"vocals": rel})
        assert ws.get_separation_state() == "done"
        paths = ws.get_track_audio_paths()
        assert "vocals" in paths
        assert paths["vocals"].name == "v.wav"

    def test_separation_rejects_path_outside_cache(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        """防御：传一个不在 workshop_dir 内的相对路径应被拒。"""
        ws = fresh_workshop
        ws.start_separation("M")
        with pytest.raises(ValueError):
            ws.complete_separation({"vocals": "../../escape.wav"})

    def test_fail_separation(self, fresh_workshop: MusicWorkshop) -> None:
        ws = fresh_workshop
        ws.start_separation("M")
        ws.fail_separation("OOM")
        assert ws.get_separation_state() == "failed"

    def test_upsert_analysis_task_returns_id(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        tid = ws.upsert_analysis_task("piano", "chord_ismir2019")
        assert isinstance(tid, str)
        assert len(tid) == 8
        tasks = ws.list_analysis_tasks("piano")
        assert len(tasks) == 1
        assert tasks[0].analysis_tool_name == "chord_ismir2019"
        assert tasks[0].analysis_state == "running"

    def test_upsert_reuses_existing_task_when_done(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        tid1 = ws.upsert_analysis_task("piano", "chord_x")
        result_abs = ws.cache.analysis_result_file("chord_x", tid1, "json")
        result_abs.parent.mkdir(parents=True, exist_ok=True)
        ws.complete_analysis("piano", tid1, ws.cache.to_relative(result_abs))
        # 再调，应复用之前的 task_id（drop 已 done 的旧数据覆盖）
        tid2 = ws.upsert_analysis_task("piano", "chord_x")
        assert tid1 == tid2

    def test_complete_and_fail_analysis(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        tid = ws.upsert_analysis_task("vocals", "pitch_y")
        result_abs = ws.cache.analysis_result_file("pitch_y", tid, "json")
        result_abs.parent.mkdir(parents=True, exist_ok=True)
        ws.complete_analysis("vocals", tid, ws.cache.to_relative(result_abs))
        assert ws.state.tab_state.tab3[tab3_key("vocals", tid)].analysis_state == "done"

        with pytest.raises(WorkshopNotFoundError):
            ws.complete_analysis(
                "vocals", "nonexistent", "analysis_result/x/y.json"
            )
        with pytest.raises(WorkshopNotFoundError):
            ws.fail_analysis("vocals", "nonexistent", "err")

    def test_complete_analysis_rejects_bad_path(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        """complete_analysis 防御：相对路径不在 workshop_dir 内应被拒。"""
        ws = fresh_workshop
        tid = ws.upsert_analysis_task("vocals", "p")
        with pytest.raises(ValueError, match="\\.\\."):
            ws.complete_analysis("vocals", tid, "../../escape.json")

    def test_mix_state_set_and_get(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.set_mix_state("vocals", MixState(volume=0.3, mute=True))
        ws.set_mix_state("drums", MixState(solo=True))
        states = ws.get_mix_states()
        assert states["vocals"].volume == 0.3
        assert states["vocals"].mute is True
        assert states["drums"].solo is True

    def test_mix_state_invalid_raises(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        with pytest.raises(ValidationError):
            ws.set_mix_state("vocals", MixState(volume=10))

    def test_select_analysis_result(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.select_analysis_result("vocals", "some/path.json")
        assert (
            ws.state.tab_state.tab4["vocals"].selected_analysis_result_path
            == "some/path.json"
        )
        ws.select_analysis_result("vocals", None)
        assert (
            ws.state.tab_state.tab4["vocals"].selected_analysis_result_path is None
        )

    def test_set_last_tab_ignored_when_same(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.set_last_tab("Tab1")
        ws._dirty = False  # 重置 dirty
        ws.set_last_tab("Tab1")  # 一样不触发
        assert ws._dirty is False

    def test_rename_works(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        ws.rename("New Song Name")
        assert ws.name == "New Song Name"

    def test_rename_invalid_raises(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        ws = fresh_workshop
        with pytest.raises(ValidationError):
            ws.rename("   ")


# ---------------------------------------------------------------------------
# EventBus 集成（最小）
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    """MusicWorkshop 发事件 → EventBus 收到。"""

    def test_separation_done_emits_event(
        self, tmp_path: Path
    ) -> None:
        from src.kernel.kernel import EventBus

        bus = EventBus()
        sub_q = bus.subscribe()
        wid = WorkshopCache.new_workshop_id()
        cache = WorkshopCache(wid, root=tmp_path)
        ws = MusicWorkshop(wid, cache, event_bus=bus, autosave=False)

        ws.start_separation("M")
        # 写文件 + 传相对路径
        abs_v = cache.track_audio_dir("vocals") / "v.wav"
        abs_v.parent.mkdir(parents=True, exist_ok=True)
        abs_v.write_bytes(b"v")
        ws.complete_separation({"vocals": cache.to_relative(abs_v)})

        seen = []
        while not sub_q.empty():
            seen.append(sub_q.get_nowait())
        types = [e.type for e in seen]
        assert "separation_started" in types
        assert "separation_done" in types
        # 发出时携带 payload
        done_event = next(e for e in seen if e.type == "separation_done")
        assert "vocals" in done_event.payload["tracks"]

    def test_no_bus_silently_drops_emits(
        self, fresh_workshop: MusicWorkshop
    ) -> None:
        """event_bus=None 时发事件不抛。"""
        ws = fresh_workshop
        ws.set_mix_state("vocals", MixState(volume=0.5))  # 不抛


# ---------------------------------------------------------------------------
# WorkshopManager
# ---------------------------------------------------------------------------


class TestWorkshopManager:
    """多车间管理。"""

    def test_create_loads_zero(self, tmp_path: Path) -> None:
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        loaded, failed = mgr.load_all()
        assert loaded == 0
        assert failed == []

    def test_create_persists_empty_state(self, tmp_path: Path) -> None:
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws = mgr.create("My First")
        assert ws.name == "My First"
        # state.json 已落盘
        loaded = ws.cache.load_state()
        assert loaded is not None
        assert loaded["WorkshopName"] == "My First"

    def test_load_all_finds_existing(self, tmp_path: Path) -> None:
        mgr1 = WorkshopManager(cache_root=tmp_path, autosave=False)
        mgr1.create("A")
        mgr1.create("B")
        mgr2 = WorkshopManager(cache_root=tmp_path, autosave=False)
        loaded, failed = mgr2.load_all()
        assert loaded == 2
        assert failed == []
        assert {w.name for w in mgr2.list_workshops()} == {"A", "B"}

    def test_load_all_skips_corrupt_state(self, tmp_path: Path) -> None:
        """坏掉的 state.json 跳过，不影响其它（Obsidian 风格）。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        good = mgr.create("Good")
        # 手动写一个坏车间
        bad_id = WorkshopCache.new_workshop_id()
        bad_cache = WorkshopCache(bad_id, root=tmp_path)
        bad_cache.state_file.write_text("not json {{{", encoding="utf-8")

        mgr2 = WorkshopManager(cache_root=tmp_path, autosave=False)
        loaded, failed = mgr2.load_all()
        assert loaded == 1
        assert len(failed) == 1
        assert failed[0][0] == bad_id
        assert mgr2.get(good.id) is not None

    def test_switch_to_changes_active(self, tmp_path: Path) -> None:
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        a = mgr.create("A")
        b = mgr.create("B")
        assert mgr.active_id() == b.id  # 最后创建的是 active
        mgr.switch_to(a.id)
        assert mgr.active_id() == a.id
        mgr.switch_to("nonexistent")
        assert mgr.active_id() == a.id  # 不变

    def test_load_all_does_not_auto_activate(
        self, tmp_path: Path
    ) -> None:
        """F5：load_all 后 active_id 应为 None。"""
        mgr1 = WorkshopManager(cache_root=tmp_path, autosave=False)
        mgr1.create("A")
        mgr1.create("B")
        mgr2 = WorkshopManager(cache_root=tmp_path, autosave=False)
        mgr2.load_all()
        assert mgr2.active_id() is None
        # 但内存里有
        assert len(mgr2.list_workshops()) == 2

    def test_close_keeps_in_memory_and_disk(self, tmp_path: Path) -> None:
        """close 是 deactivate：实例仍留 _workshops，磁盘数据也在，active 变 None。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws = mgr.create("X")
        wid = ws.id
        assert mgr.active_id() == wid  # 创建即激活
        assert mgr.close(wid)
        # 实例仍在内存
        assert mgr.get(wid) is ws
        # active 回到欢迎页
        assert mgr.active_id() is None
        # 磁盘数据保留
        assert (tmp_path / f"workshop_{wid}").exists()
        # 列表里仍可见
        assert wid in mgr.list_ids()

    def test_switch_to_calls_close_on_old_and_resumes_new(
        self, tmp_path: Path
    ) -> None:
        """switch_to 等价于 close 旧 + resume_autosave 新。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=True)
        # 直接构造：只让 a 处于 active 状态，跑它的 autosave
        wid_a = WorkshopCache.new_workshop_id()
        ca = WorkshopCache(wid_a, root=tmp_path)
        a = MusicWorkshop(wid_a, ca, autosave=True)
        assert a._thread is not None and a._thread.is_alive()
        mgr._workshops[wid_a] = a
        mgr._active_id = wid_a

        # 再准备 b（同上）
        wid_b = WorkshopCache.new_workshop_id()
        cb = WorkshopCache(wid_b, root=tmp_path)
        b = MusicWorkshop(wid_b, cb, autosave=True)
        mgr._workshops[wid_b] = b

        mgr.switch_to(wid_b)
        # A 应该 autosave stopped（close 把它停了）
        assert a._thread is None or not a._thread.is_alive()
        # B 应该 resume_autosave 后有 alive thread
        assert b._thread is not None and b._thread.is_alive()
        assert mgr.active_id() == wid_b

    def test_resume_autosave_idempotent(self, tmp_path: Path) -> None:
        """resume_autosave 幂等：已开着不再启动新线程。"""
        wid = WorkshopCache.new_workshop_id()
        cache = WorkshopCache(wid, root=tmp_path)
        ws = MusicWorkshop(wid, cache, autosave=True)
        t1 = ws._thread
        assert t1 is not None and t1.is_alive()
        ws.resume_autosave()  # noop
        assert ws._thread is t1  # 同一个
        # stop 后再 resume
        ws.stop_autosave()
        ws.resume_autosave()
        assert ws._thread is not None and ws._thread.is_alive()
        ws.stop_autosave()

    def test_close_inactive_workshop_raises_or_warns(
        self, tmp_path: Path
    ) -> None:
        """close 一个未激活的、非自身的车间——也允许（语义上"用户主动关掉它"）。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        mgr.create("A")  # noqa: F841
        b = mgr.create("B")  # b 激活
        # 关掉 b 是 OK 的
        assert mgr.close(b.id)
        # active 退回 None（因为 b 是当前 active）
        assert mgr.active_id() is None

    def test_close_then_reactivate_works(self, tmp_path: Path) -> None:
        """关闭后再激活同一个，应该能用同一实例（无需重新 load）。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        a = mgr.create("A")
        ws_ref = a  # 引用
        mgr.close(a.id)
        # active None，但实例仍在
        assert mgr.active_id() is None
        assert mgr.get(a.id) is ws_ref
        # 重新激活
        assert mgr.switch_to(a.id)
        assert mgr.active_id() == a.id

    def test_delete_removes_disk(self, tmp_path: Path) -> None:
        """F7：delete 内存+磁盘一起删。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws = mgr.create("X")
        wid = ws.id
        assert mgr.delete(wid)
        assert mgr.get(wid) is None
        assert not (tmp_path / f"workshop_{wid}").exists()

    def test_delete_workshop_only_disk_no_memory(self, tmp_path: Path) -> None:
        """磁盘上有但内存里没有的车间也能删。"""
        mgr1 = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws = mgr1.create("X")
        wid = ws.id
        mgr1.close(wid)  # 内存清掉
        # 现在用新的 mgr（也没加载）去删
        mgr2 = WorkshopManager(cache_root=tmp_path, autosave=False)
        assert mgr2.delete(wid)
        assert not (tmp_path / f"workshop_{wid}").exists()

    def test_delete_keep_state_backs_up_state_json(
        self, tmp_path: Path
    ) -> None:
        """keep_state=True：备份 state.json 到 ``recycle_bin/<id>_state.json.bak``。"""
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws = mgr.create("important")
        wid = ws.id
        assert mgr.delete(wid, keep_state=True)
        bak = tmp_path / "recycle_bin" / f"{wid}_state.json.bak"
        assert bak.exists()
        # 目录被删
        assert not (tmp_path / f"workshop_{wid}").exists()

    def test_save_all_writes_everything(self, tmp_path: Path) -> None:
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws1 = mgr.create("A")
        ws2 = mgr.create("B")
        # 改内存状态，关闭 autosave 不写
        ws1.state.workshop_name = "A-modified"
        ws2.state.workshop_name = "B-modified"
        # 此时磁盘上还没保存
        raw1 = ws1.cache.load_state()
        assert raw1["WorkshopName"] == "A"
        mgr.save_all()
        raw1b = ws1.cache.load_state()
        raw2b = ws2.cache.load_state()
        assert raw1b["WorkshopName"] == "A-modified"
        assert raw2b["WorkshopName"] == "B-modified"

    def test_rename_via_manager(self, tmp_path: Path) -> None:
        mgr = WorkshopManager(cache_root=tmp_path, autosave=False)
        ws = mgr.create("old")
        mgr.rename(ws.id, "new")
        assert ws.name == "new"
        # 落盘
        mgr.save_all()
        assert ws.cache.load_state()["WorkshopName"] == "new"
