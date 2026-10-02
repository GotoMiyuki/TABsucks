from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import torch

from src.plugins.separation.model_1.separator import SeparationPlugin, SeparatorError


class _FakeAudioSeparator:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = str(output_dir)
        self.input_path: Path | None = None
        self.output_paths: list[Path] = []

    def separate(self, input_path: str) -> list[str]:
        self.input_path = Path(input_path)
        stem_names = ("vocals", "drums", "bass", "piano", "guitar", "other")
        audio = np.zeros((16, 2), dtype=np.float32)

        for stem_name in stem_names:
            path = Path(self.output_dir) / f"{self.input_path.stem}_({stem_name})_test.wav"
            sf.write(path, audio, 8000)
            self.output_paths.append(path)

        return [path.name for path in self.output_paths]


class _UnreadableOutputSeparator(_FakeAudioSeparator):
    def separate(self, input_path: str) -> list[str]:
        self.input_path = Path(input_path)
        for stem_name in ("vocals", "drums"):
            path = Path(self.output_dir) / f"{self.input_path.stem}_({stem_name})_broken.wav"
            path.write_bytes(b"not a wav file")
            self.output_paths.append(path)
        return [path.name for path in self.output_paths]


def test_init_engine_forces_cpu_execution(monkeypatch, tmp_path: Path) -> None:
    created: list[object] = []

    class FakeEngine:
        def __init__(self, **kwargs) -> None:
            self.output_dir = kwargs["output_dir"]
            self.torch_device = torch.device("cuda")
            self.onnx_execution_provider = ["CUDAExecutionProvider"]
            self.loaded_model: str | None = None
            created.append(self)

        def load_model(self, model_name: str) -> None:
            self.loaded_model = model_name

    monkeypatch.setattr(
        "src.plugins.separation.model_1.separator.AudioSeparator",
        FakeEngine,
    )
    monkeypatch.setattr(
        "src.plugins.separation.model_1.separator._model_directory",
        lambda model_name: tmp_path,
    )
    plugin = SeparationPlugin()

    plugin._init_engine("test-model.ckpt", compute_device="cpu")

    engine = created[0]
    assert engine.torch_device.type == "cpu"
    assert engine.onnx_execution_provider == ["CPUExecutionProvider"]
    assert engine.loaded_model == "test-model.ckpt"


@pytest.mark.parametrize("failure", [RuntimeError("broken checkpoint"), SystemExit(1)])
def test_failed_model_load_can_be_retried(monkeypatch, tmp_path: Path, failure) -> None:
    created = []

    class FakeEngine:
        def __init__(self, **kwargs) -> None:
            self.loaded_model = None
            created.append(self)

        def load_model(self, model_name: str) -> None:
            if len(created) == 1:
                raise failure
            self.loaded_model = model_name

    monkeypatch.setattr(
        "src.plugins.separation.model_1.separator.AudioSeparator", FakeEngine,
    )
    monkeypatch.setattr(
        "src.plugins.separation.model_1.separator._model_directory", lambda name: tmp_path,
    )
    plugin = SeparationPlugin()
    with pytest.raises(SeparatorError, match="模型加载失败"):
        plugin._init_engine("test-model.ckpt", compute_device="cpu")

    assert plugin._separator_instance is None
    plugin._init_engine("test-model.ckpt", compute_device="cpu")
    assert len(created) == 2
    assert plugin._separator_instance.loaded_model == "test-model.ckpt"
    assert plugin._separator_device == "cpu"


def test_failed_model_switch_preserves_loaded_engine(monkeypatch, tmp_path: Path) -> None:
    class FakeEngine:
        def __init__(self, **kwargs) -> None:
            self.loaded_model = None

        def load_model(self, model_name: str) -> None:
            if model_name == "broken.ckpt":
                raise RuntimeError("broken checkpoint")
            self.loaded_model = model_name

    monkeypatch.setattr(
        "src.plugins.separation.model_1.separator.AudioSeparator", FakeEngine,
    )
    monkeypatch.setattr(
        "src.plugins.separation.model_1.separator._model_directory", lambda name: tmp_path,
    )
    plugin = SeparationPlugin()
    plugin._init_engine("good.ckpt", compute_device="cpu")
    loaded_engine = plugin._separator_instance
    with pytest.raises(SeparatorError, match="模型加载失败"):
        plugin._init_engine("broken.ckpt", compute_device="cpu")

    assert plugin._separator_instance is loaded_engine
    assert plugin._separator_model_name == "good.ckpt"
    assert loaded_engine.loaded_model == "good.ckpt"


def test_vendor_exit_during_inference_becomes_separation_error(tmp_path: Path) -> None:
    class ExitingEngine(_FakeAudioSeparator):
        def separate(self, input_path: str) -> list[str]:
            self.input_path = Path(input_path)
            raise SystemExit(1)

    engine = ExitingEngine(tmp_path)
    plugin = SeparationPlugin()
    plugin._separator_instance = engine
    with pytest.raises(SeparatorError, match="分离过程出错"):
        plugin._separate(np.zeros((2, 16), dtype=np.float32), 8000, "test-model.ckpt")

    assert engine.input_path is not None
    assert not engine.input_path.exists()


def test_separate_removes_invocation_temp_wavs(tmp_path: Path) -> None:
    engine = _FakeAudioSeparator(tmp_path)
    plugin = SeparationPlugin()
    plugin._separator_instance = engine

    audio = np.zeros((2, 16), dtype=np.float32)
    result = plugin._separate(audio, 8000, "test-model.ckpt")

    assert result.sample_rate == 8000
    assert engine.input_path is not None
    assert not engine.input_path.exists()
    assert engine.output_paths
    assert all(not path.exists() for path in engine.output_paths)


def test_separate_removes_temp_wavs_when_output_read_fails(tmp_path: Path) -> None:
    engine = _UnreadableOutputSeparator(tmp_path)
    plugin = SeparationPlugin()
    plugin._separator_instance = engine

    with pytest.raises(SeparatorError):
        plugin._separate(np.zeros((2, 16), dtype=np.float32), 8000, "test-model.ckpt")

    assert engine.input_path is not None
    assert not engine.input_path.exists()
    assert engine.output_paths
    assert all(not path.exists() for path in engine.output_paths)
