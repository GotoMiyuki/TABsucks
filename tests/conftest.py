"""pytest 全局 fixtures。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def project_root() -> Path:
    """返回项目根目录路径。"""
    # 假设 tests/ 在项目根目录下
    return Path(__file__).parent.parent


@pytest.fixture
def fixtures_dir(project_root: Path) -> Path:
    """返回测试 fixtures 目录路径。"""
    return project_root / "tests" / "fixtures"


@pytest.fixture
def synthetic_audio_data() -> tuple[np.ndarray, int]:
    """生成合成测试音频数据（正弦波）。"""
    sample_rate = 22050
    duration = 1.0  # 1 秒
    frequency = 440.0  # A4
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    samples = np.sin(2 * np.pi * frequency * t).astype(np.float32)
    return samples, sample_rate


@pytest.fixture
def synthetic_audio_data_class(synthetic_audio_data):
    """生成合成 AudioData 对象（需在 fixture 中导入以避免循环依赖）。"""
    from dataclasses import dataclass

    samples, sample_rate = synthetic_audio_data

    @dataclass(frozen=True)
    class _AudioData:
        samples: np.ndarray
        sample_rate: int
        duration: float

    return _AudioData(
        samples=samples,
        sample_rate=sample_rate,
        duration=len(samples) / sample_rate,
    )
