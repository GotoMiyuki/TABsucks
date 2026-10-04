"""Regression boundaries for P2 modularization and state schema recovery."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from src.kernel.core.cache_system import WorkshopCache
from src.kernel.core.state_migrations import CURRENT_SCHEMA_VERSION, migrate_state
from src.kernel.core.workshop_manager import WorkshopManager
from src.kernel.core.workshop_state import ValidationError, WorkshopState


def legacy_state():
    raw = WorkshopState(workshop_name="旧车间").to_dict()
    raw.pop("SchemaVersion")
    raw["TabState"]["Tab1"]["RawAudioFilePath"] = "raw_audio/song.wav"
    raw["TabState"]["Tab2"]["SelectedTracks"] = ["piano"]
    return raw


def test_migration_is_pure_and_idempotent():
    raw = legacy_state()
    snapshot = deepcopy(raw)
    migrated = migrate_state(raw)
    assert raw == snapshot
    assert migrated["SchemaVersion"] == CURRENT_SCHEMA_VERSION
    assert migrate_state(migrated) == migrated
    migrated["TabState"]["Tab2"]["SelectedTracks"].clear()
    assert raw == snapshot


@pytest.mark.parametrize("version", [-1, True, "1", 1.5, None, 2, 999])
def test_invalid_or_future_schema_rejected_without_writes(tmp_path, version):
    cache = WorkshopCache("bad", root=tmp_path)
    raw = legacy_state()
    raw["SchemaVersion"] = version
    cache.save_state(raw)
    original = cache.state_file.read_bytes()
    manager = WorkshopManager(tmp_path, autosave=False)
    loaded, failed = manager.load_all()
    manager.shutdown()
    assert loaded == 0
    assert failed[0][0] == "bad"
    assert cache.state_file.read_bytes() == original
    assert not cache.state_file.with_name("state.pre-v1.json.bak").exists()


@pytest.mark.parametrize(
    "tabs",
    [
        [],
        {"Tab1": []},
        {"Tab2": "bad"},
        {"Tab3": {"piano::id": None}},
        {"Tab4": {"piano": {"MixState": {"volume": None}}}},
    ],
)
def test_malformed_tabs_are_validation_errors(tabs):
    with pytest.raises(ValidationError):
        WorkshopState.from_dict({"TabState": tabs})


def test_migrate_backup_and_restart_recovery(tmp_path):
    cache = WorkshopCache("abc", root=tmp_path)
    raw = legacy_state()
    raw["TabState"]["Tab2"]["SeparationState"] = "running"
    raw["TabState"]["Tab3"]["piano::task"] = {"AnalysisState": "running"}
    cache.save_state(raw)
    original = cache.state_file.read_bytes()
    manager = WorkshopManager(tmp_path, autosave=False)
    assert manager.load_all() == (1, [])
    ws = manager.get("abc")
    assert ws.state.tab_state.tab1.raw_audio_file_path == "raw_audio/song.wav"
    assert ws.get_selected_tracks() == ["piano"]
    assert ws.state.tab_state.tab2.separation_state == "interrupted"
    assert ws.state.tab_state.tab3["piano::task"].analysis_state == "interrupted"
    backup = cache.state_file.with_name("state.pre-v1.json.bak")
    assert backup.read_bytes() == original
    assert cache.load_state()["SchemaVersion"] == 1
    manager.shutdown()
    assert WorkshopManager(tmp_path, autosave=False).load_all() == (1, [])
    assert backup.read_bytes() == original


def test_failed_atomic_migration_can_retry(tmp_path, monkeypatch):
    cache = WorkshopCache("abc", root=tmp_path)
    cache.save_state(legacy_state())
    original = cache.state_file.read_bytes()
    with monkeypatch.context() as patch:

        def fail_replace(*args):
            raise OSError("disk unavailable")

        patch.setattr("src.kernel.core.cache_system.os.replace", fail_replace)
        loaded, failed = WorkshopManager(tmp_path, autosave=False).load_all()
        assert loaded == 0 and failed
    assert cache.state_file.read_bytes() == original
    assert WorkshopManager(tmp_path, autosave=False).load_all() == (1, [])


def test_conflicting_backup_blocks_migration(tmp_path):
    cache = WorkshopCache("abc", root=tmp_path)
    cache.save_state(legacy_state())
    original = cache.state_file.read_bytes()
    backup = cache.state_file.with_name("state.pre-v1.json.bak")
    backup.write_bytes(b"previous backup or incomplete write")
    loaded, failed = WorkshopManager(tmp_path, autosave=False).load_all()
    assert loaded == 0 and failed
    assert cache.state_file.read_bytes() == original
    assert backup.read_bytes() == b"previous backup or incomplete write"


def test_bad_workshop_does_not_block_valid_workshop(tmp_path):
    bad = WorkshopCache("bad", root=tmp_path)
    bad.save_state({"TabState": {"Tab4": {"piano": {"MixState": {"volume": None}}}}})
    good = WorkshopCache("def", root=tmp_path)
    good.save_state(legacy_state())
    manager = WorkshopManager(tmp_path, autosave=False)
    loaded, failed = manager.load_all()
    assert loaded == 1 and manager.get("def") is not None
    assert failed[0][0] == "bad"


def test_compatibility_imports_share_implementations():
    from src.kernel.core.event_bus import EventBus
    from src.kernel.core.music_workshop import MusicWorkshop
    from src.kernel.core.plugin_manager import PluginManager
    from src.kernel.core.plugin_manager_s import SeparationPluginManager
    from src.kernel.core.resource_controller import ResourceController
    from src.kernel.core.resource_controller_s import ResourceController_s
    from src.kernel.core.workshop import MusicWorkshop as OldWorkshop
    from src.kernel.kernel import EventBus as OldBus

    assert OldBus is EventBus
    assert OldWorkshop is MusicWorkshop
    assert ResourceController_s is ResourceController
    assert SeparationPluginManager is PluginManager
    bus = EventBus()
    queue = bus.subscribe()
    bus.emit("workshop", "analysis_done", {"value": 1})
    assert queue.get_nowait().payload == {"value": 1}
    bus.unsubscribe(queue)
    assert bus.subscriber_count == 0


def test_legacy_midi_never_creates_or_overwrites_a_fake_file(tmp_path):
    from src.kernel.core.midi_exporter import MidiExporterError, export_to_midi

    output = tmp_path / "existing.mid"
    output.write_bytes(b"user file")
    with pytest.raises(MidiExporterError, match="export_chord_tracks_to_midi"):
        export_to_midi(object(), output)
    assert output.read_bytes() == b"user file"
    missing = tmp_path / "new.mid"
    with pytest.raises(MidiExporterError):
        export_to_midi(object(), missing)
    assert not missing.exists()


def test_current_schema_does_not_rewrite_or_create_backup(tmp_path, monkeypatch):
    cache = WorkshopCache("abc", root=tmp_path)
    cache.save_state(WorkshopState().to_dict())

    def unexpected_write(*args):
        pytest.fail("Current schema should not be rewritten during load")

    monkeypatch.setattr(WorkshopCache, "save_state", unexpected_write)
    assert WorkshopManager(tmp_path, autosave=False).load_all() == (1, [])
    assert not cache.state_file.with_name("state.pre-v1.json.bak").exists()


def test_analysis_engine_missing_plugin_fails_explicitly():
    from src.kernel.core.analysis_engine import AnalysisEngine, AnalysisEngineError
    from src.kernel.core.plugin_manager import PluginManagerError

    manager = MagicMock()
    manager.ensure_plugin.side_effect = PluginManagerError("missing")
    engine = AnalysisEngine(MagicMock(), manager)
    with pytest.raises(AnalysisEngineError, match="separation_bs_roformer"):
        engine._run_separation()
    manager.execute.assert_not_called()
