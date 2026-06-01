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

    def _compute_complexity(
        self, tempo_var: float, odd_meter: float, sync_score: float
    ) -> float:
        """加权分数：是否触发进阶节拍网络 (Phase C) 的判断阀值"""
        score = (tempo_var * 0.4) + (odd_meter * 0.3) + ((1.0 - sync_score) * 0.3)
        return float(np.clip(score, 0.0, 1.0))