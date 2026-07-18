"""
FoundationRhythmPlugin – pre‑separation rhythm analysis (Phase A+B)
"""
import numpy as np
from typing import List, Tuple, Dict, Any

from src.plugins import BasePlugin
from src.kernel.core import ResourceController
from src.plugins.rhythm.utils import (
    to_mono,
    extract_band_envelopes,
    estimate_global_bpm,
    build_tempo_map,
    detect_time_signature,
    calculate_band_sync,
    MADMOM_AVAILABLE,
)

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
            print(f"[{self.name}] madmom not installed, using librosa fallback (reduced accuracy).")

    def execute(self, rc: ResourceController, **kwargs) -> Dict[str, Any]:
        """
        遵循 TABsucks 架构的核心执行接口。

        优先使用 madmom DSP 管线；不可用时降级为 librosa beat tracking。
        """
        print(f"[{self.name}] Starting pre-separation rhythm analysis...")

        # 1. 资源获取
        raw_audio = rc.get_buffer("raw")
        sample_rate = rc.get_metadata("sample_rate") or 22050

        mono_samples = to_mono(raw_audio)

        if not MADMOM_AVAILABLE:
            return self._execute_librosa(rc, mono_samples, sample_rate)

        fps = 100  # DSP 节奏分析的标准帧率

        # 2. Phase A: 构建频谱并粗略分离高低频包络
        # 这里用极低 CPU 占用的 Spectral Flux 代替神经网络
        low_band_env, high_band_env = extract_band_envelopes(
            mono_samples, sample_rate, fps=fps, split_freq=150.0
        )

        # 综合特征包络
        global_env = low_band_env * 0.6 + high_band_env * 0.4

        # 3. Phase B: 专家 DSP 检测 (获取全局 BPM, 拍号, 变速等)
        global_bpm = estimate_global_bpm(global_env, fps)
        bpm_map, tempo_variance = build_tempo_map(global_env, fps, window_sec=5.0)
        time_sig, odd_meter_score = detect_time_signature(low_band_env, global_bpm, fps)
        sync_score = calculate_band_sync(low_band_env, high_band_env)

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

    def _execute_librosa(
        self, rc: ResourceController, audio: np.ndarray, sr: int
    ) -> Dict[str, Any]:
        """librosa 降级路径：onset-based BPM + 节拍检测。"""
        import librosa

        if sr != 22050:
            audio = librosa.resample(audio.astype(np.float64), orig_sr=sr, target_sr=22050)
            audio = audio.astype(np.float32)
            sr = 22050

        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        tempo_arr = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)
        global_bpm = float(tempo_arr.item() if hasattr(tempo_arr, 'item') else tempo_arr[0])

        _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)

        if len(beats) > 16:
            max_idx = min(len(beats), len(onset_env))
            beat_strengths = onset_env[beats[:max_idx]]
            ac = np.correlate(beat_strengths, beat_strengths, mode='full')
            ac = ac[len(beat_strengths) - 1:]
            candidates = {n: ac[n] for n in [3, 4, 5, 6, 7] if n < len(ac)}
            time_sig = f"{max(candidates, key=candidates.get)}/4" if candidates else "4/4"
        else:
            time_sig = "4/4"

        bpm_map = [(float(t), global_bpm) for t in np.linspace(0, len(audio) / sr, max(1, len(audio) // (sr * 10)))]
        onset_density = np.sum(onset_env > np.median(onset_env)) / max(len(onset_env), 1)
        complexity_score = float(np.clip(onset_density * 0.5 + 0.1, 0.0, 1.0))
        needs_deep = complexity_score > 0.6

        rc.set_metadata("global_bpm", global_bpm)
        rc.set_metadata("time_signature", time_sig)
        rc.set_metadata("needs_deep_rhythm_analysis", needs_deep)
        rc.set_buffer("global_onset_env", onset_env)

        print(f"[{self.name}] Librosa fallback done. BPM: {global_bpm:.1f}, TimeSig: {time_sig}")

        return {
            "status": "success",
            "data": {
                "global_bpm": global_bpm,
                "bpm_map": bpm_map,
                "time_signature_guess": time_sig,
                "complexity_score": complexity_score,
                "needs_deep_analysis": needs_deep,
            },
        }

    def _compute_complexity(
        self, tempo_var: float, odd_meter: float, sync_score: float
    ) -> float:
        """加权分数：是否触发进阶节拍网络 (Phase C) 的判断阀值"""
        score = (tempo_var * 0.4) + (odd_meter * 0.3) + ((1.0 - sync_score) * 0.3)
        return float(np.clip(score, 0.0, 1.0))