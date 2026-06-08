"""ResourceController 单元测试。"""

from __future__ import annotations

import numpy as np
import pytest


from src.kernel.core.resource_controller import ResourceController, ResourceControllerError


@pytest.fixture
def rc() -> ResourceController:
    return ResourceController()


# ------------------------------------------------------------------
# Buffer
# ------------------------------------------------------------------


class TestBuffer:
    def test_set_and_get_buffer(self, rc: ResourceController):
        data = np.zeros(1024, dtype=np.float32)
        rc.set_buffer("raw", data)
        result = rc.get_buffer("raw")
        np.testing.assert_array_equal(result, data)

    def test_get_missing_buffer_raises(self, rc: ResourceController):
        with pytest.raises(ResourceControllerError, match="Buffer 'nope' not found"):
            rc.get_buffer("nope")

    def test_overwrite_buffer(self, rc: ResourceController):
        rc.set_buffer("raw", np.array([1.0]))
        rc.set_buffer("raw", np.array([2.0, 3.0]))
        assert len(rc.get_buffer("raw")) == 2


# ------------------------------------------------------------------
# Metadata
# ------------------------------------------------------------------


class TestMetadata:
    def test_set_and_get_metadata(self, rc: ResourceController):
        rc.set_metadata("sample_rate", 22050)
        assert rc.get_metadata("sample_rate") == 22050

    def test_get_missing_metadata_returns_none(self, rc: ResourceController):
        assert rc.get_metadata("nope") is None

    def test_metadata_types(self, rc: ResourceController):
        rc.set_metadata("bpm", 120.0)
        rc.set_metadata("time_sig", "4/4")
        rc.set_metadata("beat_map", [0.0, 0.5, 1.0])
        assert rc.get_metadata("bpm") == 120.0
        assert rc.get_metadata("time_sig") == "4/4"
        assert rc.get_metadata("beat_map") == [0.0, 0.5, 1.0]


# ------------------------------------------------------------------
# Model lifecycle
# ------------------------------------------------------------------


class TestModel:
    def test_request_model_calls_loader(self, rc: ResourceController):
        sentinel = object()
        model = rc.request_model("test_model", lambda name: sentinel)
        assert model is sentinel

    def test_request_model_caches(self, rc: ResourceController):
        call_count = 0

        def loader(name):
            nonlocal call_count
            call_count += 1
            return "model_obj"

        rc.request_model("m", loader)
        rc.request_model("m", loader)
        assert call_count == 1

    def test_release_model(self, rc: ResourceController):
        rc.request_model("m", lambda name: "model")
        rc.release_model("m")
        # after release, request should call loader again
        call_count = 0

        def loader2(name):
            nonlocal call_count
            call_count += 1
            return "new_model"

        rc.request_model("m", loader2)
        assert call_count == 1

    def test_release_nonexistent_model_no_error(self, rc: ResourceController):
        rc.release_model("never_loaded")  # should not raise

    def test_release_all_models(self, rc: ResourceController):
        rc.request_model("a", lambda n: "A")
        rc.request_model("b", lambda n: "B")
        rc.release_all_models()
        call_count = 0

        def loader(n):
            nonlocal call_count
            call_count += 1
            return n

        rc.request_model("a", loader)
        rc.request_model("b", loader)
        assert call_count == 2

    def test_get_current_device_returns_device(self, rc: ResourceController):
        device = rc.get_current_device()
        # should be either a torch.device or "cpu"
        assert device is not None


# ------------------------------------------------------------------
# Clear
# ------------------------------------------------------------------


class TestClear:
    def test_clear_resets_everything(self, rc: ResourceController):
        rc.set_buffer("raw", np.array([1.0]))
        rc.set_metadata("sr", 44100)
        rc.request_model("m", lambda n: "model")
        rc.clear()
        with pytest.raises(ResourceControllerError):
            rc.get_buffer("raw")
        assert rc.get_metadata("sr") is None
        call_count = 0

        def loader(n):
            nonlocal call_count
            call_count += 1
            return "new"

        rc.request_model("m", loader)
        assert call_count == 1
