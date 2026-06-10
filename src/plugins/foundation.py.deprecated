import os
import sys
import numpy as np
import madmom
from madmom.audio.signal import Signal
from madmom.audio.stft import ShortTimeFourierTransform
from madmom.audio.spectrogram import Spectrogram
from madmom.features.tempo import TempoEstimation
from typing import List, Tuple, Dict, Any
from dataclasses import dataclass, asdict

MADMOM_AVAILABLE = False
try:
    # 解决 Python 3.10+ 兼容性
    import collections
    if sys.version_info >= (3, 10):
        import collections.abc
        collections.MutableSequence = collections.abc.MutableSequence
        collections.Iterable = collections.abc.Iterable
        collections.Mapping = collections.abc.Mapping
    
    # 解决 numpy 废弃类型兼容
    if not hasattr(np, 'float'): np.float = float
    if not hasattr(np, 'int'): np.int = int

    from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
    from madmom.features.tempo import TempoEstimationProcessor
    
    MADMOM_AVAILABLE = True
except ImportError as e:
    # 可以在这里记录日志，提醒用户安装依赖
    pass

from src.plugins import Plugin

@dataclass
class FoundationRhythmResult:
    """A+B 阶段输出的标准化数据结构"""
    global_bpm: float
    bpm_map: List[Tuple[float, float]]       # [(时间戳_秒, 局部BPM), ...]
    time_signature_guess: str                # 预估拍号，例如 "4/4", "7/8", "Mixed"
    complexity_score: float                  # 0.0 ~ 1.0，分数越高代表律动越复杂
    needs_deep_analysis: bool                # 核心开关：是否建议调用 BS-RoFormer (阶段C)
    onset_envelope: np.ndarray               # 粗提的起始点包络（供后续插件复用，避免重复计算）

class FoundationRhythmPlugin(Plugin):
    """
    基础节奏探测插件 (Phase A + B)
    基于 madmom DSP 算子的极速频带感知起始点检测、拍号预判与复杂度评估。
    """

    @property
    def name(self) -> str:
        return "rhythm_foundation"

    @property
    def version(self) -> str:
        return "1.1.0_madmom_dsp"
    
    def __init__(self):
        super().__init__()
        self.device = self._get_device()
        self._init_models()

    def _get_device(self) -> str:
        # --- 借鉴 ChordMini 的【硬件感应】 ---
        # 虽然 A+B 阶段多用 CPU，但保留这个逻辑方便未来扩展
        return "cpu" # 目前 madmom 主要是 CPU 运算

    def _init_models(self):
        """参考 ChordMini 的【延迟加载】与【错误捕获】"""
        if not MADMOM_AVAILABLE:
            return
        try:
            self.act_processor = RNNBeatProcessor()
            self.tempo_estimator = TempoEstimationProcessor(fps=100)
            self.beat_tracker = DBNBeatTrackingProcessor(fps=100)
        except Exception as e:
            print(f"Failed to load Madmom models: {e}")
            self.active = False
            
    def execute(self, audio_data, **kwargs) -> dict:
        """
        执行 A+B 混合分析。
        """
        mono_samples = self._to_mono(audio_data.samples)
        sample_rate = audio_data.sample_rate

        # 预设帧率 (fps)，madmom 默认通常使用 100 fps 用于节奏分析
        fps = 100 
        
        # 1 & 2. Phase A: 构建频谱并分离低/中高频包络 (Spectral Flux)
        # 避免两次计算 STFT，我们先生成全局 STFT/Spectrogram，再按频段切片
        low_band_env, high_band_env = self._extract_band_envelopes(mono_samples, sample_rate, fps=fps, split_freq=150.0)
        
        # 综合全局特征包络 (低频底鼓/贝斯权重略高)
        global_env = low_band_env * 0.6 + high_band_env * 0.4 
        
        # 极速 DSP 算法计算全局 BPM
        global_bpm = self._estimate_global_bpm(global_env, fps)

        # 3. Phase B: 专家检测 (Expert Analysis)
        # 3.1 滑动窗口分析：生成 Tempo Map 并检测变速
        bpm_map, tempo_variance = self._build_tempo_map(global_env, fps, window_sec=5.0)
        
        # 3.2 拍号与奇数拍预判 (基于低频包络自相关与全局 BPM)
        time_sig, odd_meter_score = self._detect_time_signature(low_band_env, global_bpm, fps)
        
        # 3.3 多轨道律动同步率 (Polyrhythm detection)
        sync_score = self._calculate_band_sync(low_band_env, high_band_env)

        # 4. 评估复杂性
        complexity_score = self._compute_complexity(tempo_variance, odd_meter_score, sync_score)
        
        # 如果律动极度复杂 (> 0.6)，建议召唤分离模型 (如 BS-RoFormer) 进阶分析
        needs_deep = complexity_score > 0.6

        # 5. 返回结果
        result = FoundationRhythmResult(
            global_bpm=float(global_bpm),
            bpm_map=bpm_map,
            time_signature_guess=time_sig,
            complexity_score=float(complexity_score),
            needs_deep_analysis=bool(needs_deep),
            onset_envelope=global_env
        )
        
        return asdict(result)

    # ==========================================
    # Madmom DSP 核心逻辑
    # ==========================================

    def _to_mono(self, samples: np.ndarray) -> np.ndarray:
        """如果为多声道则混音为单声道"""
        if samples.ndim > 1:
            return np.mean(samples, axis=1 if samples.shape[1] == 2 else 0)
        return samples

    def _extract_band_envelopes(self, samples: np.ndarray, sr: int, fps: int, split_freq: float) -> Tuple[np.ndarray, np.ndarray]:
        """
        利用 madmom 一次性计算 Spectrogram 并分离频段计算 Spectral Flux（起始点包络）。
        这是极速且高精度的 DSP 做法，规避了调用沉重 RNN 模型。
        """
        # 构建 Madmom 的 STFT 和 Spectrogram 框架
        sig = Signal(samples, sample_rate=sr)
        frame_size = 2048
        hop_size = int(sr / fps)
        
        stft = ShortTimeFourierTransform(sig, frame_size=frame_size, hop_size=hop_size)
        spec = Spectrogram(stft)
        
        # 计算每帧正向频谱差分 (Spectral Flux)
        diff = np.diff(spec, axis=0)
        diff[diff < 0] = 0  # 半波整流，只保留能量增强的部分（起音点）
        
        # 补齐因差分丢失的第一帧
        diff = np.vstack((np.zeros(diff.shape[1]), diff))
        
        # 按频率切分 bins
        freqs = spec.bin_frequencies
        low_bins = freqs <= split_freq
        high_bins = freqs > split_freq
        
        # 分别对低频/高频 bin 求和并做归一化
        low_env = np.sum(diff[:, low_bins], axis=1)
        high_env = np.sum(diff[:, high_bins], axis=1)
        
        # 避免除以零的极小值防护
        low_env = low_env / (np.max(low_env) + 1e-8)
        high_env = high_env / (np.max(high_env) + 1e-8)
        
        return low_env, high_env

    def _estimate_global_bpm(self, env: np.ndarray, fps: int) -> float:
        """使用 madmom 的 TempoEstimation (基于梳状滤波/自相关) 极速获取全局 BPM"""
        tempo_est = TempoEstimation(fps=fps, min_bpm=60.0, max_bpm=240.0)
        tempos = tempo_est(env)
        
        if tempos.shape[0] > 0:
            return tempos[0, 0]  # 返回置信度最高的 BPM
        return 120.0

    def _build_tempo_map(self, env: np.ndarray, fps: int, window_sec: float) -> Tuple[List[Tuple[float, float]], float]:
        """
        滑动窗口分析局部 BPM，并计算变异系数判断变速复杂度。
        """
        win_frames = int(window_sec * fps)
        hop_frames = win_frames // 2  # 50% 覆盖率的滑动窗口
        
        bpm_map =[]
        bpms =[]
        tempo_est = TempoEstimation(fps=fps, min_bpm=60.0, max_bpm=240.0)
        
        for i in range(0, len(env) - win_frames, hop_frames):
            win_env = env[i : i + win_frames]
            if np.sum(win_env) < 1e-3: 
                continue  # 跳过纯静音段
                
            tempos = tempo_est(win_env)
            if tempos.shape[0] > 0:
                bpm = tempos[0, 0]
                timestamp = i / fps
                bpm_map.append((timestamp, bpm))
                bpms.append(bpm)
                
        if not bpm_map:
            return [(0.0, 120.0)], 0.0
            
        # 计算 BPM 的变异系数 (CV = std / mean) 评估变速程度
        mean_bpm = np.mean(bpms)
        tempo_variance = np.std(bpms) / mean_bpm if mean_bpm > 0 else 0.0
        
        return bpm_map, min(tempo_variance * 5.0, 1.0) # 放大系数以映射到 0~1 区间

    def _detect_time_signature(self, low_env: np.ndarray, global_bpm: float, fps: int) -> Tuple[str, float]:
        """
        基于极速 DSP 的自相关分析：
        通过分析低频包络在 [拍间距] 的 3、4、5、7 倍处的自相关峰值，来判定小节循环长度。
        """
        if global_bpm <= 0: return "4/4", 0.0
        
        # 计算一拍所占的帧数
        ibi_frames = int((60.0 / global_bpm) * fps)
        if ibi_frames <= 0: return "4/4", 0.0
        
        # 计算低频包络的自相关 (只计算需要探测的最大滞后长度)
        max_lag = ibi_frames * 8
        if len(low_env) < max_lag:
            return "4/4", 0.0
            
        acorr = np.correlate(low_env, low_env, mode='full')
        # 截取正向部分
        acorr = acorr[len(low_env)-1 : len(low_env)-1 + max_lag]
        
        # 探测不同拍号的嫌疑分子
        candidate_meters = [3, 4, 5, 7]
        meter_scores = {}
        
        for m in candidate_meters:
            lag = ibi_frames * m
            if lag < len(acorr):
                # 获取该拍号长度处的循环相关性峰值
                meter_scores[m] = acorr[lag]
                
        if not meter_scores:
            return "4/4", 0.0
            
        best_meter = max(meter_scores, key=meter_scores.get)
        
        # 奇数拍打分机制
        if best_meter in [5, 7]:
            odd_meter_score = 1.0
        elif best_meter == 3:
            odd_meter_score = 0.5
        else:
            odd_meter_score = 0.0
            
        return f"{best_meter}/4", odd_meter_score

    def _calculate_band_sync(self, low_env: np.ndarray, high_env: np.ndarray) -> float:
        """
        计算低频(Bass/Kick)与中高频(Snare/Hihat)的皮尔逊相关系数。
        分数越低，说明频段在时间轴上越错位（大量切分音/多重节奏/幽灵音）。
        """
        # 使用 numpy 原生方法计算皮尔逊相关系数
        corr = np.corrcoef(low_env, high_env)[0, 1]
        if np.isnan(corr):
            return 1.0
        # 保证结果落在 0 ~ 1 之间 (处理负相关为非同步)
        return float(np.clip(corr, 0.0, 1.0))

    def _compute_complexity(self, tempo_var: float, odd_meter: float, sync_score: float) -> float:
        """
        贝斯手数字直觉矩阵：
        变速(tempo_var) 占 40%
        奇数拍(odd_meter) 占 30%
        律动交错度(1 - sync_score) 占 30%
        """
        score = (tempo_var * 0.4) + (odd_meter * 0.3) + ((1.0 - sync_score) * 0.3)
        return float(np.clip(score, 0.0, 1.0))