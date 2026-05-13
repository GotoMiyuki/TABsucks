# 伪代码，位于 kernel/core/analysis_engine.py
def run_analysis(self):
    pm = self.plugin_manager
    rc = self.resource_controller

    # 1. 分离（假设已完成，stems已在缓冲区中）
    # 2. 调用节奏基础插件（已完成）
    # 3. 对钢琴和吉他分别进行和弦识别
    chord_plugin_id = pm.get_plugin("chord_ismir2019")
    for stem in ["piano", "guitar"]:
        pm.execute_plugin(chord_plugin_id, stem_name=stem)

    # 4. 对贝斯进行根音检测
    bass_plugin_id = pm.get_plugin("chord_bass_root")
    pm.execute_plugin(bass_plugin_id)

    # 5. 读取各插件结果，送入多级纠偏器 (Level-1/2)
    piano_chords = rc.get_metadata("chord_raw_piano")
    bass_root = rc.get_metadata("bass_root")
    self.refiner.refine(piano_chords, bass_root, ...)
"""
可能要和上面的合并
"""
def analyze(self):
    # ... 已完成分离和基础节奏 ...
    
    # 调用和弦插件
    chord_plugin_id = self.plugin_manager.get_plugin_id("chord_ismir2019")
    for stem in ["piano", "guitar"]:
        result = self.plugin_manager.execute_plugin(
            chord_plugin_id, 
            stem_name=stem
        )
        print(f"Chord on {stem}: {result['data']}")
    
    # 然后将多轨结果送入 refiner
    piano_chords = self.rc.get_metadata("chord_raw_piano")
    guitar_chords = self.rc.get_metadata("chord_raw_guitar")
    rhythm_data = self.rc.get_metadata("rhythm_foundation_data")
    refined = self.refiner.refine_chord_sequence(piano_chords, rhythm_data)

"""
和弦后处理/纠偏模块 (Level-1)
利用基础节奏结果对原始和弦预测进行节拍对齐和起音约束。
"""
import numpy as np
import librosa
from typing import List, Tuple, Dict, Any

def frames_to_time(frame_indices: np.ndarray, hop_length: int, sr: int) -> np.ndarray:
    """将帧索引转换为时间(秒)"""
    return librosa.frames_to_time(frame_indices, sr=sr, hop_length=hop_length)


def beat_sync_chroma(chroma: np.ndarray, 
                     bpm: float,
                     sr: int = 22050,
                     hop_length: int = 512,
                     beats_per_aggregation: int = 1) -> np.ndarray:
    """
    将帧级色度向量聚合为节拍同步色度。
    
    Args:
        chroma: shape (n_frames, 12)
        bpm: 全局 BPM
        sr: 采样率
        hop_length: 色度提取时的 hop 长度
        beats_per_aggregation: 每多少拍聚合一次 (1=每拍, 2=每两拍)
    Returns:
        beat_chroma: shape (n_beats, 12)
    """
    n_frames = chroma.shape[0]
    frame_times = frames_to_time(np.arange(n_frames), hop_length, sr)
    
    # 每拍的秒数
    beat_duration = 60.0 / bpm
    agg_duration = beat_duration * beats_per_aggregation
    
    # 将帧分配到对应的"节拍组"
    beat_groups = (frame_times // agg_duration).astype(int)
    n_beats = int(np.ceil(np.max(frame_times) / agg_duration))
    
    beat_chroma = np.zeros((n_beats, chroma.shape[1]))
    for beat_idx in range(n_beats):
        mask = beat_groups == beat_idx
        if np.any(mask):
            beat_chroma[beat_idx] = np.median(chroma[mask], axis=0)
    
    return beat_chroma


def onset_constrained_transition(
    chord_indices: np.ndarray,
    onset_envelope: np.ndarray,
    hop_length: int,
    sr: int,
    onset_threshold: float = 0.1,
    alpha: float = 5.0
) -> np.ndarray:
    """
    利用起音包络约束和弦解码中的转移代价。
    
    当 onset_envelope[t] 很小时，惩罚标签变化。
    这模拟了文中的公式：
        new_cost = original_cost - alpha * onset_envelope[t]
    
    Args:
        chord_indices: 原始模型输出的最佳标签序列，shape (n_frames,)
        onset_envelope: 包络强度，shape (n_frames,)
        hop_length: 特征提取的 hop 长度
        sr: 采样率
        onset_threshold: 低于此值的帧会被惩罚
        alpha: 惩罚系数
    Returns:
        smoothed_indices: 平滑后的标签序列
    """
    if len(chord_indices) != len(onset_envelope):
        # 如果长度不一致，重新采样包络
        from scipy.interpolate import interp1d
        original_times = np.linspace(0, len(onset_envelope)/100, len(onset_envelope))  # onset 以 100 fps 运行
        target_times = librosa.frames_to_time(np.arange(len(chord_indices)), sr=sr, hop_length=hop_length)
        interp_func = interp1d(original_times, onset_envelope, kind='linear', 
                              bounds_error=False, fill_value=0.0)
        onset_envelope = interp_func(target_times)
    
    smoothed = np.copy(chord_indices)
    for t in range(1, len(chord_indices)):
        if onset_envelope[t] < onset_threshold:
            # 如果起音很弱，保持前一帧的标签
            smoothed[t] = smoothed[t-1]
    
    return smoothed

def refine_chord_sequence(
    raw_chords: Dict[str, Any],  # {"labels": [...], "frame_times": [...]}
    rhythm_data: Dict[str, Any],  # FoundationRhythmResult 的数据部分
    sr: int = 22050,
    hop_length: int = 512
) -> Dict[str, Any]:
    """
    Level-1 纠偏主函数，由 AnalysisEngine 调用。
    
    Args:
        raw_chords: 和弦模型原始输出，必须包含 "labels" (每帧标签) 和 "frame_times" (时间)
        rhythm_data: FoundationRhythmPlugin 返回的 data 字典
        sr: 音频采样率
        hop_length: 和弦特征提取的 hop 长度
    Returns:
        与 raw_chords 结构相同的精炼结果
    """
    labels = np.array(raw_chords["labels"])
    global_bpm = rhythm_data.get("global_bpm", 120.0)
    onset_env = np.array(rhythm_data.get("onset_envelope", []))
    
    # 步骤1：节拍同步聚合（如果需要每拍一个和弦）
    # 这里假设原始 labels 是帧级的，我们先转为节拍级
    # 注意：这需要原始模型也输出置信度矩阵，此处简化处理
    # 实际使用时可能需要保留帧级结果，只做平滑
    # beat_labels = beat_sync_labels(...)  # 具体实现取决于你的模型输出格式
    
    # 步骤2：起音约束平滑
    if len(onset_env) > 0:
        smoothed_labels = onset_constrained_transition(
            labels, onset_env, hop_length, sr
        )
    else:
        smoothed_labels = labels
    
    return {
        "labels": smoothed_labels.tolist(),
        "frame_times": raw_chords["frame_times"],
        "smoothed": True,
        "method": "onset_constrained"
    }