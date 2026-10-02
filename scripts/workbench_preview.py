"""Isolated UI preview with generated audio; never touches user projects or models.

Run: python scripts/workbench_preview.py --seed --port 8012
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from pathlib import Path

import numpy as np
import uvicorn
from fastapi.responses import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.kernel.kernel import Kernel
from src.plugins._example_analyzer import ExampleAnalyzerPlugin
from src.plugins._example_separator import ExampleSeparatorPlugin
from src.ui.server import make_app


class PreviewAnalyzer(ExampleAnalyzerPlugin):
    """Deterministic preview jobs: bass fails, other stems return known chords."""

    PLUGIN_NAME = "chord_preview"

    def execute(self, rc, **kwargs):
        kwargs["durations_sec"] = 6.0
        result = super().execute(rc, **kwargs)
        if kwargs.get("stem_name") == "bass":
            return {"status": "failed", "error": "预览样例：贝斯分析失败，可重试；其他音轨仍可用"}
        return result


class PreviewSeparator(ExampleSeparatorPlugin):
    def execute(self, rc, **kwargs):
        kwargs["durations_sec"] = 12.0
        return super().execute(rc, **kwargs)


def install_preview_jobs(kernel):
    """Only for this isolated server; no changes to production plugin registration."""
    orchestrator = kernel.orchestrator
    build_context = orchestrator._build_context

    def register(context):
        context.pm.register(PreviewAnalyzer())
        context.pm.register(PreviewSeparator())
        return context

    orchestrator._build_context = lambda: register(build_context())
    register(orchestrator._default_context)
    list_analyzers = kernel.list_analyzer_plugins
    kernel.list_analyzer_plugins = lambda: [
        *list_analyzers(),
        {
            "name": "chord_preview",
            "display_name": "预览和弦样例（不进行模型推理）",
            "input_stems": ["vocals", "drums", "bass", "piano", "guitar", "other"],
            "mock": True,
        },
    ]


def build_preview(root: Path, seed: bool, seconds: int = 180, demo_jobs: bool = False):
    kernel = Kernel(cache_root=root, autosave=False)
    kernel.boot()
    if demo_jobs:
        install_preview_jobs(kernel)
    if seed and not kernel.manager.list_workshops():
        info = kernel.create_workshop("试听示例 · 部分分析完成")
        ws = kernel.manager.get(info["id"])
        sample_rate = 8000
        t = np.arange(sample_rate * seconds, dtype=np.float32) / sample_rate
        tracks = ["vocals", "drums", "bass", "piano", "guitar", "other"]
        paths = {}
        for index, track in enumerate(["full", *tracks]):
            signal = (
                np.sin(2 * math.pi * (110 + index * 55) * t)
                * 0.12
                * (0.45 + 0.55 * np.sin(t * 2) ** 2)
            )
            path = root / f"preview-{track}.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes((signal * 32767).astype("<i2").tobytes())
            if track == "full":
                ws.set_raw_audio(path, "试听示例.wav")
            else:
                paths[track] = ws.cache.to_relative(ws.cache.save_track_audio(track, path))
        ws.start_separation("preview")
        ws.complete_separation(paths)
        ws.set_selected_tracks(["bass", "guitar"])
        task = ws.upsert_analysis_task("guitar", "chord_ismir2019")
        result = ws.cache.workshop_dir / "preview-chords.json"
        result.write_text(
            json.dumps(
                {
                    "chords": [
                        {"start": i, "end": i + 12, "name": ["C", "Am", "F", "G"][i // 12 % 4]}
                        for i in range(0, seconds, 12)
                    ]
                }
            ),
            encoding="utf-8",
        )
        ws.complete_analysis("guitar", task, ws.cache.to_relative(result))
        ws.set_last_tab("Tab4")
        ws.save()
    return kernel


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--theme", choices=["system", "light", "dark"], default="system")
    parser.add_argument("--demo-jobs", action="store_true")
    parser.add_argument("--cache-root", type=Path, default=Path(".workbench-preview"))
    args = parser.parse_args()
    app = make_app(build_preview(args.cache_root, args.seed, args.seconds, args.demo_jobs))
    if args.theme != "system":
        # Preview-only stylesheet emulation; the production UI always follows the OS.
        @app.middleware("http")
        async def preview_theme(request, call_next):
            if request.url.path == "/static/css/workbench.css":
                path = Path(__file__).resolve().parents[1] / "src/ui/static/css/workbench.css"
                condition = " all" if args.theme == "dark" else " not all"
                css = path.read_text(encoding="utf-8").replace(
                    "(prefers-color-scheme:dark)", condition
                )
                return Response(css, media_type="text/css", headers={"Cache-Control": "no-store"})
            return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=args.port)
