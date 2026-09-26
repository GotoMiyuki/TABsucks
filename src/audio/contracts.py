"""Explicit in-memory audio convention: float32 (channels, samples)."""

from __future__ import annotations

from typing import Literal

import numpy as np


AudioLayout = Literal["channels_first", "samples_first"]


def as_audio(
    samples: np.ndarray, sample_rate: int, *,
    layout: AudioLayout = "channels_first",
) -> np.ndarray:
    """Validate and return float32 channels-first audio without shape guessing."""
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    array = np.asarray(samples)
    if array.ndim == 1:
        array = array[np.newaxis, :]
    elif array.ndim == 2 and layout == "samples_first":
        array = array.T
    elif array.ndim != 2 or layout != "channels_first":
        raise ValueError("audio must be mono or two-dimensional with an explicit layout")
    if not 1 <= array.shape[0] <= 8 or array.shape[1] == 0:
        raise ValueError(f"invalid audio shape: {array.shape}")
    result = array.astype(np.float32, copy=False)
    if not np.isfinite(result).all():
        raise ValueError("audio contains NaN or infinite samples")
    return result


def to_mono(samples: np.ndarray) -> np.ndarray:
    """Convert validated channels-first audio to one float32 channel."""
    array = np.asarray(samples)
    if array.ndim != 2 or not 1 <= array.shape[0] <= 8:
        raise ValueError("expected (channels, samples) audio")
    if array.shape[0] == 1:
        return array[0].astype(np.float32, copy=False)
    return array.mean(axis=0, dtype=np.float32)


__all__ = ["as_audio", "to_mono", "AudioLayout"]
