"""Mock 分析插件（generic，模拟"和弦识别"输出）。

**用途**：MV P阶段用于 Tab3 下拉列表 + 端到端冒烟测试。

与 :py:mod:`src.plugins._example_separator` 同等地位：本插件不被 PM 自动
扫盘（前缀 ``_example_``），但 ``PluginManager.register(example_analyzer)`` 可显式登记。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.plugins import BasePlugin

logger = logging.getLogger(__name__)

EXAMPLE_ANALYSIS_STEPS: int = 50
EXAMPLE_ANALYSIS_DELAY: float = 0.03  # 秒/步 → 默认 1.5 秒


class ExampleAnalyzerPlugin(BasePlugin):
    """Mock 分析器：输出固定 4 个和弦。"""

    PLUGIN_NAME: str = "example_analyzer"
    PLUGIN_DISPLAY_NAME: str = "Mock Analyzer (范例)"
    PLUGIN_VERSION: str = "0.0.1"

    @property
    def name(self) -> str:
        return self.PLUGIN_NAME

    @property
    def version(self) -> str:
        return self.PLUGIN_VERSION

    def execute(
        self,
        rc,
        **kwargs,
    ) -> dict[str, Any]:
        cb = kwargs.get("progress_callback")
        durations_sec = float(kwargs.get("durations_sec", 1.5))
        duration_sec_per_step = durations_sec / EXAMPLE_ANALYSIS_STEPS

        # 拿 stem 数据
        try:
            rc.get_buffer(kwargs.get("stem_name", "vocals"))
            sample_rate = rc.get_metadata("sample_rate") or 22050
        except Exception:
            sample_rate = 22050

        # 模拟进度
        for i in range(EXAMPLE_ANALYSIS_STEPS + 1):
            if cb is not None:
                try:
                    cb(i / EXAMPLE_ANALYSIS_STEPS)
                except Exception:
                    pass
            if i < EXAMPLE_ANALYSIS_STEPS:
                time.sleep(duration_sec_per_step)

        # 输出固定 4 个和弦
        chords = [
            {"start": 0.0, "end": 4.0, "name": "C:maj", "root": "C", "quality": "maj"},
            {"start": 4.0, "end": 8.0, "name": "A:min", "root": "A", "quality": "min"},
            {"start": 8.0, "end": 12.0, "name": "F:maj", "root": "F", "quality": "maj"},
            {"start": 12.0, "end": 16.0, "name": "G:maj", "root": "G", "quality": "maj"},
        ]

        return {
            "status": "success",
            "data": {
                "plugin": self.PLUGIN_NAME,
                "version": self.PLUGIN_VERSION,
                "chords": chords,
                "sample_rate": sample_rate,
                "mock": True,
            },
        }


async def run_async(
    rc,
    *,
    progress_callback=None,
    durations_sec: float = 1.5,
) -> dict[str, Any]:
    steps = EXAMPLE_ANALYSIS_STEPS
    for i in range(steps + 1):
        if progress_callback is not None:
            try:
                progress_callback(i / steps)
            except Exception:
                pass
        if i < steps:
            await asyncio.sleep(durations_sec / steps)

    return {
        "status": "success",
        "data": {
            "plugin": "example_analyzer",
            "chords": [
                {"start": 0.0, "end": 4.0, "name": "C:maj"},
                {"start": 4.0, "end": 8.0, "name": "A:min"},
            ],
            "mock": True,
        },
    }


__all__ = [
    "EXAMPLE_ANALYSIS_STEPS",
    "EXAMPLE_ANALYSIS_DELAY",
    "ExampleAnalyzerPlugin",
    "run_async",
]
