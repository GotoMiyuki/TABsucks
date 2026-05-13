import sys
import numpy as np
from typing import List, Tuple, Dict, Any
from dataclasses import dataclass, asdict

# 解决 Madmom 在 Python 3.10+ 下的兼容性问题
try:
    import collections
    if sys.version_info >= (3, 10):
        import collections.abc
        collections.MutableSequence = collections.abc.MutableSequence
        collections.Iterable = collections.abc.Iterable
        collections.Mapping = collections.abc.Mapping
    
    if not hasattr(np, 'float'): np.float = float
    if not hasattr(np, 'int'): np.int = int

    from madmom.audio.signal import Signal
    from madmom.audio.stft import ShortTimeFourierTransform
    from madmom.audio.spectrogram import Spectrogram
    from madmom.features.tempo import TempoEstimation
    
    MADMOM_AVAILABLE = True
except ImportError as e:
    MADMOM_AVAILABLE = False
    print(f"[RhythmFoundation] Warning: madmom not installed. {e}")

# 导入 TABsucks 的基础插件类与资源控制器类型
from src.plugins import BasePlugin
from src.kernel.core import ResourceController

class FoundationRhythmPlugin(BasePlugin):
    """
    基础节奏探测插件 (Phase A + B)
    运行于音轨分离 (Separator) 之前！
    职责：对原曲进行高低频粗切分的极速 DSP 分析，定下全局速度基调，并评估律动复杂度。
    """

    @property
    def name(self) -> str:
        return "rhythm_foundation"

    @property
    def version(self) -> str:
        return "1.2.0_tabsucks_rc"
    
    def __init__(self):
        super().__init__()
        if not MADMOM_AVAILABLE:
            self.active = False
            print(f"[{self.name}] Plugin disabled due to missing madmom.")

    def execute(self, rc: ResourceController, **kwargs) -> Dict[str, Any]:
        """
        遵循 TABsucks 架构的核心执行接口。
        """
        print(f"[{self.name}] Starting pre-separation rhythm analysis...")

        # 1. 资源获取 (从 RC 提取未分离的原始混音)
        # 这时 BS-RoFormer 还没有运行，内存里只有原曲数据
        raw_audio = rc.get_buffer("raw")
        sample_rate = rc.get_metadata("sample_rate") or 22050
        
        mono_samples = self._to_mono(raw_audio)
        fps = 100 # DSP 节奏分析的标准帧率
        
        # 2. Phase A: 构建频谱并粗略分离高低频包络
        # 这里用极低 CPU 占用的 Spectral Flux 代替神经网络
        low_band_env, high_band_env = self._extract_band_envelopes(
            mono_samples, sample_rate, fps=fps, split_freq=150.0
        )
        
        # 综合特征包络
        global_env = low_band_env * 0.6 + high_band_env * 0.4 
        
        # 3. Phase B: 专家 DSP 检测 (获取全局 BPM, 拍号, 变速等)
        global_bpm = self._estimate_global_bpm(global_env, fps)
        bpm_map, tempo_variance = self._build_tempo_map(global_env, fps, window_sec=5.0)
        time_sig, odd_meter_score = self._detect_time_signature(low_band_env, global_bpm, fps)
        sync_score = self._calculate_band_sync(low_band_env, high_band_env)

        # 4. 复杂度评估 (决定是否触发 Phase C)
        complexity_score = self._compute_complexity(tempo_variance, odd_meter_score, sync_score)
        
        # 核心逻辑：如果复杂度 > 0.6，建议 AnalysisEngine (AE) 后续挂载深度分析插件
        needs_deep = complexity_score > 0.6

        # ==========================================
        # 5. 回写状态到 RC (Resource Controller)
        # ==========================================
        # 将基础数据写入全局元数据，这样后面的 Chord 插件和 UI 都可以直接读取，不用重复计算
        rc.set_metadata("global_bpm", float(global_bpm))
        rc.set_metadata("time_signature", time_sig)
        rc.set_metadata("needs_deep_rhythm_analysis", bool(needs_deep))
        
        # 将算好的起音包络放进 Buffer，如果触发了 Phase C，它可以直接拿去用
        rc.set_buffer("global_onset_env", global_env)

        print(f"[{self.name}] Analysis done. BPM: {global_bpm}, TimeSig: {time_sig}, NeedsDeep: {needs_deep}")

        # 6. 返回规范化结果给 PluginManager / AnalysisEngine
        return {
            "status": "success",
            "data": {
                "global_bpm": float(global_bpm),
                "bpm_map": bpm_map,
                "time_signature_guess": time_sig,
                "complexity_score": float(complexity_score),
                "needs_deep_analysis": bool(needs_deep)
            }
        }

    # ==========================================
    # 纯 DSP 算子区域 (无须动用重型模型 / GPU)
    # ==========================================

    def _to_mono(self, samples: np.ndarray) -> np.ndarray:
        if samples.ndim > 1:
            return np.mean(samples, axis=1 if samples.shape[1] == 2 else 0)
        return samples

    def _extract_band_envelopes(self, samples: np.ndarray, sr: int, fps: int, split_freq: float) -> Tuple[np.ndarray, np.ndarray]:
        """使用 madmom STFT 快速计算低高频包络 (Spectral Flux)"""
        sig = Signal(samples, sample_rate=sr)
        frame_size = 2048
        hop_size = int(sr / fps)
        
        stft = ShortTimeFourierTransform(sig, frame_size=frame_size, hop_size=hop_size)
        spec = Spectrogram(stft)
        
        diff = np.diff(spec, axis=0)
        diff[diff < 0] = 0  # 半波整流
        diff = np.vstack((np.zeros(diff.shape[1]), diff)) # 补齐第一帧
        
        freqs = spec.bin_frequencies
        low_bins = freqs <= split_freq
        high_bins = freqs > split_freq
        
        low_env = np.sum(diff[:, low_bins], axis=1)
        high_env = np.sum(diff[:, high_bins], axis=1)
        
        # 极小值防护归一化
        low_env = low_env / (np.max(low_env) + 1e-8)
        high_env = high_env / (np.max(high_env) + 1e-8)
        
        return low_env, high_env

    def _estimate_global_bpm(self, env: np.ndarray, fps: int) -> float:
        """极速梳状滤波评估 BPM"""
        tempo_est = TempoEstimation(fps=fps, min_bpm=60.0, max_bpm=240.0)
        tempos = tempo_est(env)
        if tempos.shape[0] > 0:
            return float(tempos[0, 0])
        return 120.0

    def _build_tempo_map(self, env: np.ndarray, fps: int, window_sec: float) -> Tuple[List[Tuple[float, float]], float]:
        """滑窗极速获取局部变异速度 (不锁死显存，纯 CPU 秒级执行)"""
        win_frames = int(window_sec * fps)
        hop_frames = win_frames // 2
        
        bpm_map = []
        bpms =[]
        tempo_est = TempoEstimation(fps=fps, min_bpm=60.0, max_bpm=240.0)
        
        for i in range(0, len(env) - win_frames, hop_frames):
            win_env = env[i : i + win_frames]
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

    def _detect_time_signature(self, low_env: np.ndarray, global_bpm: float, fps: int) -> Tuple[str, float]:
        """基于低频自相关分析，快速预估拍号"""
        if global_bpm <= 0: return "4/4", 0.0
        ibi_frames = int((60.0 / global_bpm) * fps)
        if ibi_frames <= 0: return "4/4", 0.0
        
        max_lag = ibi_frames * 8
        if len(low_env) < max_lag:
            return "4/4", 0.0
            
        acorr = np.correlate(low_env, low_env, mode='full')
        acorr = acorr[len(low_env)-1 : len(low_env)-1 + max_lag]
        
        candidate_meters =[3, 4, 5, 7]
        meter_scores = {}
        for m in candidate_meters:
            lag = ibi_frames * m
            if lag < len(acorr):
                meter_scores[m] = acorr[lag]
                
        if not meter_scores:
            return "4/4", 0.0
            
        best_meter = max(meter_scores, key=meter_scores.get)
        odd_meter_score = 1.0 if best_meter in[5, 7] else (0.5 if best_meter == 3 else 0.0)
        
        return f"{best_meter}/4", odd_meter_score

    def _calculate_band_sync(self, low_env: np.ndarray, high_env: np.ndarray) -> float:
        """皮尔逊相关系数算高低频重合度 (越低越复杂)"""
        corr = np.corrcoef(low_env, high_env)[0, 1]
        if np.isnan(corr):
            return 1.0
        return float(np.clip(corr, 0.0, 1.0))

    def _compute_complexity(self, tempo_var: float, odd_meter: float, sync_score: float) -> float:
        """加权分数：是否触发进阶节拍网络 (Phase C) 的判断阀值"""
        score = (tempo_var * 0.4) + (odd_meter * 0.3) + ((1.0 - sync_score) * 0.3)
        return float(np.clip(score, 0.0, 1.0))