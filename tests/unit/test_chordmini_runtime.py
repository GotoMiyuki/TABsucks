"""Regression coverage for the real, isolated ChordMini runtime and shipped weights."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.plugins.chord.chordmini_runtime import REQUIRED_SOURCE_FILES, load_btc_runtime


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/plugins/chord/external/chordmini/src"


@pytest.fixture()
def source_copy(tmp_path):
    for relative in REQUIRED_SOURCE_FILES:
        original = SOURCE / relative
        assert original.is_file(), "Initialize ChordMini with the README submodule setup command"
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    return tmp_path


def test_missing_source_reports_setup_command(tmp_path):
    with pytest.raises(FileNotFoundError, match="git submodule update --init"):
        load_btc_runtime(tmp_path)


@pytest.mark.parametrize("preload_utils", [False, True])
def test_real_runtime_keeps_application_namespace_and_excludes_evaluation_imports(preload_utils):
    script = f"""
import sys
import src
if {preload_utils!r}:
    import src.utils
before_path = list(src.__path__)
before_sys_path = list(sys.path)
before_utils = sys.modules.get('src.utils')
from src.plugins.chord.btc_sl import _ensure_imports
_ensure_imports()
assert list(src.__path__) == before_path
assert sys.path == before_sys_path
from src.utils.naming import sanitize_title
assert sanitize_title('song') == 'song'
if before_utils is not None:
    assert sys.modules['src.utils'] is before_utils
assert all(name not in sys.modules for name in ('mir_eval', 'matplotlib', 'seaborn', 'sklearn'))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr


def test_concurrent_first_load_returns_one_complete_runtime(source_copy):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(load_btc_runtime, [source_copy] * 4))
    assert all(runtime is results[0] for runtime in results)
    assert callable(results[0].predict_sliding_windows)


def test_failed_import_can_be_retried_without_partial_modules(source_copy):
    model_file = source_copy / "models/btc_model.py"
    original = model_file.read_text(encoding="utf-8")
    model_file.write_text("raise ImportError('temporary model error')", encoding="utf-8")
    with pytest.raises(ImportError, match="temporary model error"):
        load_btc_runtime(source_copy)
    model_file.write_text(original, encoding="utf-8")
    runtime = load_btc_runtime(source_copy)
    model = runtime.model_class(runtime.config_class())
    assert len(model.state_dict()) == 221


@pytest.mark.parametrize("filename", ["btc_model_large_voca.pt", "btc_model_best.pth"])
def test_shipped_checkpoints_load_all_model_tensors(filename, monkeypatch):
    from src.kernel.core.resource_controller import ResourceController
    from src.plugins.chord.btc_sl import BTCSLChordPlugin

    checkpoint = SOURCE.parent / "checkpoints" / filename
    assert checkpoint.is_file(), "Initialize ChordMini to obtain the shipped checkpoints"
    rc = ResourceController()
    monkeypatch.setattr(rc, "get_current_device", lambda: "cpu")
    model, mean, std = BTCSLChordPlugin()._init_model(rc, checkpoint_path=str(checkpoint))
    assert len(model.state_dict()) == 221
    assert not model.training
    assert std > 0
