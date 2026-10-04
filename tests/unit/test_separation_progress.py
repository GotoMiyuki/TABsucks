from types import SimpleNamespace

import numpy as np
import pytest
import requests
import torch

from src.plugins.separation.model_1.progress import ProgressAudioSeparator, roformer_chunk_count


def adapter():
    engine = ProgressAudioSeparator.__new__(ProgressAudioSeparator)
    events = []
    engine.progress_callback = lambda progress, **details: events.append({"progress": progress, **details})
    return engine, events


class Response:
    status_code = 200

    def __init__(self, chunks, headers=None):
        self.chunks = chunks
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def test_download_reports_real_bytes_and_publishes_complete_file(monkeypatch, tmp_path):
    engine, events = adapter()
    destination = tmp_path / "checkpoint.ckpt"
    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response([b"abc", b"defg"], {"content-length": "7"}))
    engine.download_file_if_not_exists("https://example.test/model", destination)
    assert destination.read_bytes() == b"abcdefg"
    assert events[-1]["completed"] == events[-1]["total"] == 7
    assert events[-1]["progress"] == 1
    assert events[-1]["stage"] == "downloading_model"
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("chunks", [[b"abc"], [b"abc", requests.ConnectionError("interrupted")]])
def test_interrupted_download_is_not_cached_and_can_retry(monkeypatch, tmp_path, chunks):
    engine, _ = adapter()
    destination = tmp_path / "checkpoint.ckpt"
    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response(chunks, {"content-length": "7"}))
    with pytest.raises((RuntimeError, requests.ConnectionError)):
        engine.download_file_if_not_exists("https://example.test/model", destination)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.part"))
    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response([b"abcdefg"], {"content-length": "7"}))
    engine.download_file_if_not_exists("https://example.test/model", destination)
    assert destination.read_bytes() == b"abcdefg"


@pytest.mark.parametrize("headers", [{}, {"content-length": "2", "content-encoding": "gzip"}])
def test_download_without_decoded_size_is_indeterminate(monkeypatch, tmp_path, headers):
    engine, events = adapter()
    destination = tmp_path / "model.json"
    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response([b"decoded data"], headers))
    engine.download_file_if_not_exists("https://example.test/model", destination)
    assert destination.read_bytes() == b"decoded data"
    assert events[-1]["progress"] is None
    assert events[-1]["completed"] == 12
    assert events[-1]["total"] is None


def native_model():
    config = SimpleNamespace(
        model=SimpleNamespace(stft_hop_length=2),
        inference=SimpleNamespace(dim_t=6),
        audio=SimpleNamespace(sample_rate=1, hop_length=2),
        training=SimpleNamespace(instruments=["bass", "drums"]),
    )
    return SimpleNamespace(
        is_roformer=True, pitch_shift=0, override_model_segment_size=False,
        segment_size=3, overlap=8, model_data_cfgdict=config,
        model_run=torch.nn.Linear(1, 1),
    )


def test_inference_reports_completed_forward_passes_and_removes_hook():
    engine, events = adapter()
    model = native_model()
    assert roformer_chunk_count(model, 25) == 4

    def demix(mix):
        for _ in range(0, mix.shape[-1], 8):
            model.model_run(torch.ones(1, 1))
        return {"bass": mix}

    result = engine._demix_with_progress(model, demix, mix=np.zeros((2, 25)))
    assert "bass" in result
    assert [e["completed"] for e in events] == [0, 1, 2, 3, 4]
    assert [e["progress"] for e in events] == [0, .25, .5, .75, 1]
    assert not model.model_run._forward_hooks


def test_inference_error_removes_hook_without_publishing_completion():
    engine, events = adapter()
    model = native_model()

    def demix(mix):
        model.model_run(torch.ones(1, 1))
        raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError, match="inference failed"):
        engine._demix_with_progress(model, demix, mix=np.zeros((2, 25)))
    assert events[-1]["progress"] == .25
    assert not model.model_run._forward_hooks


def test_unknown_inference_layout_does_not_guess_progress():
    engine, events = adapter()
    model = native_model()
    model.is_roformer = False
    engine._demix_with_progress(model, lambda mix: mix, mix=np.zeros((2, 25)))
    assert len(events) == 1
    assert events[0]["progress"] is None
    assert events[0]["total"] is None


def test_stage_counters_reset_and_instance_methods_are_restored(monkeypatch):
    engine, events = adapter()

    class Model:
        def __init__(self):
            self.__dict__.update(vars(native_model()))

        def demix(self, mix):
            for _ in range(0, mix.shape[-1], 8):
                self.model_run(torch.ones(1, 1))
            return mix

        def final_process(self, path, source, name):
            return {name: source}

    engine.model_instance = Model()

    def separate(self, path):
        mix = self.model_instance.demix(mix=np.zeros((2, 25)))
        for name in ("bass", "drums"):
            self.model_instance.final_process(name, mix, name)
        return ["bass", "drums"]

    monkeypatch.setattr("audio_separator.separator.Separator.separate", separate)
    assert engine.separate("input.wav") == ["bass", "drums"]
    writes = [e for e in events if e["stage"] == "writing_stems"]
    assert [e["completed"] for e in writes] == [0, 1, 2]
    assert "demix" not in vars(engine.model_instance)
    assert "final_process" not in vars(engine.model_instance)


def test_task_snapshot_retains_progress_and_cancel_is_not_overwritten():
    from src.kernel.core.task_service import TaskService

    service = TaskService()
    record, _ = service.admit("w", "separation", ("model",))
    record.status = "running"
    service.report_progress(record, .5, stage="separating_audio", completed=2, total=4, unit="chunks")
    assert record.snapshot()["progress_detail"]["completed"] == 2
    assert record.snapshot()["stage"] == "separating_audio"
    record.status = record.stage = "cancelling"
    assert not service.report_progress(record, 1, stage="saving_results")
    assert record.stage == "cancelling"
