"""范例 plugin 的单元测试。"""

from __future__ import annotations

import numpy as np


# Mock 一个 minimal ResourceController for testing
class _StubRC:
    """Minimal RC stub: 只实现 example plugin 用的接口。"""

    def __init__(self) -> None:
        self._buffers: dict[str, np.ndarray] = {
            "raw": np.zeros(22050 * 3, dtype=np.float32)
        }
        self._metadata: dict[str, object] = {"sample_rate": 22050}

    def get_buffer(self, name: str) -> np.ndarray:
        if name not in self._buffers:
            raise KeyError(name)
        return self._buffers[name]

    def set_buffer(self, name: str, data: np.ndarray) -> None:
        self._buffers[name] = data

    def get_metadata(self, key: str):
        return self._metadata.get(key)

    def set_metadata(self, key: str, value) -> None:
        self._metadata[key] = value


class TestExampleSeparatorPlugin:
    def test_name_and_version(self) -> None:
        from src.plugins._example_separator import ExampleSeparatorPlugin

        p = ExampleSeparatorPlugin()
        assert p.name == "example_separator"
        assert p.version == "0.0.1"

    def test_execute_returns_standard_dict(self) -> None:
        from src.plugins._example_separator import ExampleSeparatorPlugin

        rc = _StubRC()
        plugin = ExampleSeparatorPlugin()
        result = plugin.execute(rc, durations_sec=0.0)  # 0 秒 = 跳过 sleep
        assert result["status"] == "success"
        assert result["data"]["plugin"] == "example_separator"
        assert set(result["data"]["stems"]) == {
            "vocals", "drums", "bass", "piano", "guitar", "other"
        }

    def test_execute_writes_6_stems_to_rc(self) -> None:
        from src.plugins._example_separator import ExampleSeparatorPlugin

        rc = _StubRC()
        plugin = ExampleSeparatorPlugin()
        plugin.execute(rc, durations_sec=0.0)
        for name in ["vocals", "drums", "bass", "piano", "guitar", "other"]:
            arr = rc.get_buffer(name)
            assert isinstance(arr, np.ndarray)
            assert arr.shape[0] == 22050 * 3
            assert arr.dtype == np.float32

    def test_progress_callback_called_with_0_to_1(self) -> None:
        from src.plugins._example_separator import ExampleSeparatorPlugin

        rc = _StubRC()
        plugin = ExampleSeparatorPlugin()
        progress = []
        result = plugin.execute(
            rc,
            durations_sec=0.0,
            progress_callback=lambda p: progress.append(p),
        )
        assert result["status"] == "success"
        # 至少包含起始 0.0 与结束 1.0
        assert progress[0] == 0.0
        assert progress[-1] == 1.0
        # 中间值都在 [0, 1]
        for p in progress:
            assert 0.0 <= p <= 1.0

    def test_run_async_returns_dict(self) -> None:
        import asyncio

        from src.plugins._example_separator import run_async

        rc = _StubRC()
        result = asyncio.run(run_async(rc, durations_sec=0.0))
        assert result["status"] == "success"
        assert result["data"]["mock"] is True


class TestExampleAnalyzerPlugin:
    def test_basic(self) -> None:
        from src.plugins._example_analyzer import ExampleAnalyzerPlugin

        plugin = ExampleAnalyzerPlugin()
        assert plugin.name == "example_analyzer"
        assert plugin.version == "0.0.1"

    def test_execute_returns_4_chords(self) -> None:
        from src.plugins._example_analyzer import ExampleAnalyzerPlugin

        rc = _StubRC()
        plugin = ExampleAnalyzerPlugin()
        result = plugin.execute(rc, durations_sec=0.0)
        assert result["status"] == "success"
        chords = result["data"]["chords"]
        assert len(chords) == 4
        assert chords[0]["name"] == "C:maj"
        assert chords[1]["name"] == "A:min"

    def test_progress_callback(self) -> None:
        from src.plugins._example_analyzer import ExampleAnalyzerPlugin

        rc = _StubRC()
        plugin = ExampleAnalyzerPlugin()
        progress = []
        plugin.execute(rc, durations_sec=0.0, progress_callback=lambda p: progress.append(p))
        assert progress[0] == 0.0
        assert progress[-1] == 1.0


class TestGetManifest:
    def test_separator_manifest_shape(self) -> None:
        from src.plugins._example_separator import get_manifest

        m = get_manifest()
        assert m["name"] == "example_separator"
        assert m["phase"] == "separation"
        assert m["mock"] is True
        assert "requirements" in m
