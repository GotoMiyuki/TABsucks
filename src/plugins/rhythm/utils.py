"""
Rhythm DSP utilities (band envelopes, BPM, time signature, sync)
"""
import sys
import numpy as np
from typing import List, Tuple
from collections import namedtuple

# ---------- madmom 兼容性补丁 ----------
try:
    import collections
    if sys.version_info >= (3, 10):
        import collections.abc
        collections.MutableSequence = collections.abc.MutableSequence
        collections.Iterable = collections.abc.Iterable
        collections.Mapping = collections.abc.Mapping
    if not hasattr(np, 'float'):
        np.float = float
    if not hasattr(np, 'int'):
        np.int = int

    from madmom.audio.signal import Signal
    from madmom.audio.stft import ShortTimeFourierTransform
    from madmom.audio.spectrogram import Spectrogram
    from madmom.features.tempo import TempoEstimation
    MADMOM_AVAILABLE = True
except ImportError as e:
    MADMOM_AVAILABLE = False
    print(f"[RhythmUtils] madmom not installed: {e}")
# --------------------------------------------

def to_mono(samples: np.ndarray) -> np.ndarray:
    """单声道处理"""
    if samples.ndim > 1:
        return np.mean(samples, axis=1 if samples.shape[1] == 2 else 0)
    return samples

def extract_band_envelopes(
    samples: np.ndarray,
    sample_rate: int,
    fps: int = 100,
    split_freq: float = 150.0
) -> Tuple[np.ndarray, np.ndarray]:
    """使用 madmom STFT 快速计算低高频包络 (Spectral Flux)"""
    sig = Signal(samples, sample_rate=sample_rate)
    frame_size = 2048
    hop_size = int(sample_rate / fps)
    stft = ShortTimeFourierTransform(sig, frame_size=frame_size, hop_size=hop_size)
    spec = Spectrogram(stft)

    diff = np.diff(spec, axis=0)
    diff[diff < 0] = 0  # half‑wave rectification
    diff = np.vstack((np.zeros(diff.shape[1]), diff))

    freqs = spec.bin_frequencies
    low_bins = freqs <= split_freq
    high_bins = freqs > split_freq

    low_env = np.sum(diff[:, low_bins], axis=1)
    high_env = np.sum(diff[:, high_bins], axis=1)

    # normalize to avoid division by zero
    low_env /= (np.max(low_env) + 1e-8)
    high_env /= (np.max(high_env) + 1e-8)
    return low_env, high_env

def estimate_global_bpm(env: np.ndarray, fps: int) -> float:
    """极速梳状滤波评估 BPM"""
    if not MADMOM_AVAILABLE:
        return 120.0
    tempo_est = TempoEstimation(fps=fps, min_bpm=60.0, max_bpm=240.0)
    tempos = tempo_est(env)
    if tempos.shape[0] > 0:
        return float(tempos[0, 0])
    return 120.0

def build_tempo_map(
    env: np.ndarray,
    fps: int,
    window_sec: float = 5.0
) -> Tuple[List[Tuple[float, float]], float]:
    """
    Returns:
        bpm_map: 表 of (timestamp_sec, local_bpm)
        tempo_variance: bpm变速衡量指标 (0‑1)
    """
    if not MADMOM_AVAILABLE:
        return [(0.0, 120.0)], 0.0

    win_frames = int(window_sec * fps)
    hop_frames = win_frames // 2
    bpm_map = []
    bpms = []
    tempo_est = TempoEstimation(fps=fps, min_bpm=60.0, max_bpm=240.0)

    for i in range(0, len(env) - win_frames, hop_frames):
        win_env = env[i: i + win_frames]
        if np.sum(win_env) < 1e-3:
            continue
        tempos = tempo_est(win_env)
        if tempos.shape[0] > 0:
            bpm = tempos[0, 0]
            timestamp = i / fps
            bpm_map.append((timestamp, float(bpm)))
            bpms.append(bpm)

    if not bpm_map:
        return [(0.0, 120.0)], 0.0

    mean_bpm = np.mean(bpms)
    tempo_variance = np.std(bpms) / mean_bpm if mean_bpm > 0 else 0.0
    return bpm_map, float(min(tempo_variance * 5.0, 1.0))

def detect_time_signature(
    low_env: np.ndarray,
    global_bpm: float,
    fps: int
) -> Tuple[str, float]:
    """基于低频自相关分析，快速预估拍号"""
    if global_bpm <= 0:
        return "4/4", 0.0
    ibi_frames = int((60.0 / global_bpm) * fps)
    if ibi_frames <= 0:
        return "4/4", 0.0
    max_lag = ibi_frames * 8
    if len(low_env) < max_lag:
        return "4/4", 0.0

    acorr = np.correlate(low_env, low_env, mode='full')
    acorr = acorr[len(low_env) - 1: len(low_env) - 1 + max_lag]

    candidate_meters = [3, 4, 5, 7]
    meter_scores = {}
    for m in candidate_meters:
        lag = ibi_frames * m
        if lag < len(acorr):
            meter_scores[m] = acorr[lag]

    if not meter_scores:
        return "4/4", 0.0

    best_meter = max(meter_scores, key=meter_scores.get)
    odd_meter_score = (
        1.0 if best_meter in [5, 7] else
        0.5 if best_meter == 3 else
        0.0
    )
    return f"{best_meter}/4", odd_meter_score

def calculate_band_sync(low_env: np.ndarray, high_env: np.ndarray) -> float:
    """皮尔逊相关系数算高低频重合度 (越低越复杂)"""
    corr = np.corrcoef(low_env, high_env)[0, 1]
    if np.isnan(corr):
        return 1.0
    return float(np.clip(corr, 0.0, 1.0))