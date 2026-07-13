"""kernel 模块的单元测试。"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from queue import Empty

import numpy as np
import pytest

from src.kernel.core.workshop import MixState  # noqa: E402
from src.kernel.kernel import EventBus, Kernel

# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


class TestEventBus:
    """进程级事件总线。"""

    def test_emit_and_subscribe(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        bus.emit("ws1", "separation_done", {"tracks": ["vocals"]})
        ev = q.get(timeout=1.0)
        assert ev.workshop_id == "ws1"
        assert ev.type == "separation_done"
        assert ev.payload["tracks"] == ["vocals"]

    def test_multiple_subscribers(self) -> None:
        """每个订阅者都应收到事件。"""
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.emit("ws1", "test_event")
        # 两个 q 都应收到一份
        e1 = q1.get(timeout=0.5)
        e2 = q2.get(timeout=0.5)
        assert e1.type == e2.type == "test_event"

    def test_unsubscribe_stops_delivery(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.emit("ws1", "after_unsub")
        with pytest.raises(Empty):
            q.get_nowait()
        # subscriber_count 应归零
        assert bus.subscriber_count == 0

    def test_subscriber_count(self) -> None:
        bus = EventBus()
        assert bus.subscriber_count == 0
        q1 = bus.subscribe()
        bus.subscribe()  # noqa: F841
        assert bus.subscriber_count == 2
        bus.unsubscribe(q1)
        assert bus.subscriber_count == 1

    def test_emit_never_raises_on_full_queue(self) -> None:
        """即使某个订阅者出问题，也不影响其他订阅者。"""
        bus = EventBus()
        q_good = bus.subscribe()

        # 制造一个会抛异常的订阅者
        class BadQueue:
            def put_nowait(self, ev):  # noqa: D401
                raise RuntimeError("boom")

        bad_q = BadQueue()  # type: ignore[assignment]
        bus._subscribers.append(bad_q)  # type: ignore[arg-type]

        # emit 不应抛
        bus.emit("ws1", "test")
        ev = q_good.get(timeout=0.5)
        assert ev.type == "test"

    def test_event_payload_default_empty(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        bus.emit("ws1", "ev")
        ev = q.get(timeout=0.5)
        assert ev.payload == {}

    def test_event_has_emitted_at(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        before = time.time()
        bus.emit("ws1", "ev")
        ev = q.get(timeout=0.5)
        after = time.time()
        assert before <= ev.emitted_at <= after


# ---------------------------------------------------------------------------
# Kernel 顶层
# ---------------------------------------------------------------------------


@pytest.fixture
def kernel_in_tmp(tmp_path: Path) -> Kernel:
    """一个使用 tmp_path 作 cache 根、bus 实时创建的 Kernel 实例。"""
    k = Kernel(cache_root=tmp_path, autosave=False)
    k.boot()
    return k


class TestKernel:
    """Kernel 顶层装配。"""

    def test_boot_no_workshops(self, tmp_path: Path) -> None:
        k = Kernel(cache_root=tmp_path, autosave=False)
        loaded, failed = k.boot()
        assert loaded == 0
        assert failed == []

    def test_list_workshops_empty(self, kernel_in_tmp: Kernel) -> None:
        assert kernel_in_tmp.list_workshops() == []

    def test_create_workshop(self, kernel_in_tmp: Kernel) -> None:
        info = kernel_in_tmp.create_workshop("Test")
        assert info["name"] == "Test"
        assert "id" in info
        assert kernel_in_tmp.list_workshops()[0]["name"] == "Test"

    def test_switch_workshop(self, kernel_in_tmp: Kernel) -> None:
        a = kernel_in_tmp.create_workshop("A")
        kernel_in_tmp.create_workshop("B")  # noqa: F841
        assert kernel_in_tmp.switch_workshop(a["id"]) is True
        assert kernel_in_tmp.switch_workshop("nonexistent") is False

    def test_get_state(self, kernel_in_tmp: Kernel) -> None:
        info = kernel_in_tmp.create_workshop("MySong")
        state = kernel_in_tmp.get_state(info["id"])
        assert state is not None
        assert state["WorkshopName"] == "MySong"

    def test_get_state_missing(self, kernel_in_tmp: Kernel) -> None:
        assert kernel_in_tmp.get_state("nope") is None

    def test_delete_workshop(self, kernel_in_tmp: Kernel) -> None:
        info = kernel_in_tmp.create_workshop("X")
        assert kernel_in_tmp.delete_workshop(info["id"])
        # 在 list_workshops 里应消失
        assert kernel_in_tmp.list_workshops() == []

    def test_close_then_reactivate(self, kernel_in_tmp: Kernel) -> None:
        """close 是 deactivate：实例仍留，列表可见，可以再激活。"""
        info = kernel_in_tmp.create_workshop("Persisted")
        wid = info["id"]
        assert kernel_in_tmp.close_workshop(wid)
        # 列表里仍在（不 pop）
        assert len(kernel_in_tmp.list_workshops()) == 1
        assert kernel_in_tmp.get_state(wid) is not None
        # 文件还在
        # 重新激活
        assert kernel_in_tmp.switch_workshop(wid)
        # active 又变 wid
        # 再切别的：等价于 close wid + activate other
        a = kernel_in_tmp.create_workshop("Other")
        assert kernel_in_tmp.switch_workshop(wid)
        # 现在 wid 是 active；a 也还在 _workshops 里
        assert wid in [w["id"] for w in kernel_in_tmp.list_workshops()]
        assert a["id"] in [w["id"] for w in kernel_in_tmp.list_workshops()]

    def test_close_then_persist_across_reboot(
        self, kernel_in_tmp: Kernel, tmp_path: Path
    ) -> None:
        """关闭后状态正常落盘，重启后能恢复。"""
        info = kernel_in_tmp.create_workshop("Persisted")
        wid = info["id"]
        # 改点东西再关
        kernel_in_tmp.rename_workshop(wid, "Modified")
        kernel_in_tmp.close_workshop(wid)
        # 新 kernel boot 后能加载到
        k2 = Kernel(cache_root=tmp_path, autosave=False)
        loaded, _ = k2.boot()
        assert loaded == 1
        assert k2.get_state(wid)["WorkshopName"] == "Modified"

    def test_rename_via_kernel(self, kernel_in_tmp: Kernel) -> None:
        info = kernel_in_tmp.create_workshop("Old")
        assert kernel_in_tmp.rename_workshop(info["id"], "New")
        assert kernel_in_tmp.get_state(info["id"])["WorkshopName"] == "New"

    def test_suggest_name(self, kernel_in_tmp: Kernel) -> None:
        """F8：suggest_workshop_name helper。"""
        # 本地路径
        assert kernel_in_tmp.suggest_workshop_name("/music/sunset.mp3") == "sunset"
        # 标题清理
        assert (
            kernel_in_tmp.suggest_workshop_name("周杰伦 - 晴天 (Official MV)")
            == "周杰伦 - 晴天"
        )
        # URL 兜底
        assert (
            "watch" in kernel_in_tmp.suggest_workshop_name(
                "https://youtube.com/watch?v=abc"
            ).lower()
            or "abc" in kernel_in_tmp.suggest_workshop_name(
                "https://youtube.com/watch?v=abc"
            )
        )
        # None → 默认名
        assert kernel_in_tmp.suggest_workshop_name(None) == "New Workshop"

    def test_subscribe_events(self, kernel_in_tmp: Kernel) -> None:
        q = kernel_in_tmp.subscribe_events()
        # 触发事件（返回值不需要，只需要副作用）
        kernel_in_tmp.create_workshop("EventTest")
        seen_types: list[str] = []
        deadline = time.time() + 1.0
        while time.time() < deadline and "workshop_created" not in seen_types:
            try:
                ev = q.get(timeout=0.1)
                seen_types.append(ev.type)
            except Empty:
                pass
        assert "workshop_created" in seen_types

    def test_start_separation_task_persists_tab2_tracks(
        self,
        kernel_in_tmp: Kernel,
    ) -> None:
        info = kernel_in_tmp.create_workshop("Separate Me")
        wid = info["id"]
        samples = np.zeros(22050, dtype=np.float32)

        async def runner():
            await kernel_in_tmp.start_separation_task(
                wid,
                plugin_name="example_separator",
                audio_samples=samples,
                sample_rate=22050,
                durations_sec=0.0,
            )

        asyncio.run(runner())

        ws = kernel_in_tmp.manager.get(wid)  # type: ignore[union-attr]
        assert ws is not None
        assert ws.get_separation_state() == "done"
        track_paths = ws.get_track_audio_paths()
        assert "vocals" in track_paths
        assert "other" in track_paths
        assert track_paths["vocals"].is_file()

    def test_recovers_raw_audio_path_from_disk_state(
        self,
        kernel_in_tmp: Kernel,
    ) -> None:
        info = kernel_in_tmp.create_workshop("Recover Raw")
        ws = kernel_in_tmp.manager.get(info["id"])  # type: ignore[union-attr]
        assert ws is not None
        raw_path = ws.set_raw_audio_from_bytes(b"fake audio", "song.mp3")

        ws.state.tab_state.tab1.raw_audio_file_path = None
        recovered = Kernel._recover_workshop_raw_audio_path(ws)

        assert recovered == raw_path
        assert Path(ws.state.tab_state.tab1.raw_audio_file_path) == Path(
            "raw_audio/song.mp3"
        )

    def test_load_workshop_raw_audio_uses_multi_channel_loader(
        self,
        kernel_in_tmp: Kernel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        info = kernel_in_tmp.create_workshop("Stereo Raw")
        ws = kernel_in_tmp.manager.get(info["id"])  # type: ignore[union-attr]
        assert ws is not None
        ws.set_raw_audio_from_bytes(b"fake audio", "song.wav")

        from src.audio.loader import AudioData

        stereo = np.zeros((2, 128), dtype=np.float32)

        def fake_load_audio_multi_channel(path):
            assert path.name == "song.wav"
            return AudioData(samples=stereo, sample_rate=48000, duration=128 / 48000)

        monkeypatch.setattr(
            "src.audio.loader.load_audio_multi_channel",
            fake_load_audio_multi_channel,
        )

        kernel_in_tmp._load_workshop_raw_audio_into_rc(
            info["id"],
            sample_rate=22050,
        )

        orch = kernel_in_tmp._require_orchestrator()
        np.testing.assert_array_equal(orch.rc.get_buffer("raw"), stereo)
        assert orch.rc.get_metadata("sample_rate") == 48000

    def test_require_manager_before_boot_raises(self, tmp_path: Path) -> None:
        k = Kernel(cache_root=tmp_path, autosave=False)
        with pytest.raises(RuntimeError, match="未启动"):
            k.list_workshops()

    def test_run_and_shutdown(self, tmp_path: Path) -> None:
        """run 是个占位阻塞循环，shutdown 通过 _shutdown.set 优雅退出。"""
        k = Kernel(cache_root=tmp_path, autosave=False)
        k.boot()

        # 在另一个线程调 run，1 秒后 shutdown
        t = threading.Thread(target=k.run, daemon=True)
        t.start()
        time.sleep(0.2)
        k.shutdown()
        t.join(timeout=2.0)
        assert not t.is_alive()

    def test_shutdown_idempotent(self, tmp_path: Path) -> None:
        k = Kernel(cache_root=tmp_path, autosave=False)
        k.boot()
        k.shutdown()
        # 第二次调用不应报错
        k.shutdown()


# ---------------------------------------------------------------------------
# 集成测试：Kernel + WorkshopManager + EventBus 端到端
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """真实场景：创建 → 加载原音频 → 分离 → 分析 → 混音。"""

    def test_full_flow(self, tmp_path: Path) -> None:
        k = Kernel(cache_root=tmp_path, autosave=False)
        k.boot()

        # 1. 创建车间
        ws_info = k.create_workshop("Sunset")
        ws = k.manager.get(ws_info["id"])  # type: ignore[union-attr]
        assert ws is not None

        # 2. 加载原音频
        src = tmp_path / "raw.mp3"
        src.write_bytes(b"raw mp3")
        ws.set_raw_audio(src)

        # 3. 跑分离
        ws.start_separation("BS-RoFormer-SW")
        stem_vocals = ws.cache.track_audio_dir("vocals") / "vocals.wav"
        stem_vocals.parent.mkdir(parents=True, exist_ok=True)
        stem_vocals.write_bytes(b"v")
        ws.complete_separation({"vocals": ws.cache.to_relative(stem_vocals)})

        assert ws.get_separation_state() == "done"

        # 4. 跑和弦分析
        tid = ws.upsert_analysis_task("vocals", "chord_ismir2019")
        result_abs = ws.cache.analysis_result_file("chord_ismir2019", tid, "json")
        result_abs.parent.mkdir(parents=True, exist_ok=True)
        ws.complete_analysis("vocals", tid, ws.cache.to_relative(result_abs))

        tasks = ws.list_analysis_tasks("vocals")
        assert tasks[0].analysis_state == "done"

        # 5. 调 Tab4 混音
        ws.set_mix_state("vocals", MixState(volume=0.3, mute=True))
        assert ws.get_mix_states()["vocals"].mute is True

        # 6. 重启后状态能恢复
        k.shutdown()
        k2 = Kernel(cache_root=tmp_path, autosave=False)
        loaded, failed = k2.boot()
        assert loaded == 1
        assert failed == []
        recovered = k2.manager.get(ws_info["id"])  # type: ignore[union-attr]
        assert recovered is not None
        assert recovered.name == "Sunset"
        assert recovered.get_separation_state() == "done"
        # 跨平台：用 Path 比较
        assert Path(recovered.state.tab_state.tab1.raw_audio_file_path) == Path(
            "raw_audio/raw.mp3"
        )
        assert recovered.get_mix_states()["vocals"].volume == 0.3
