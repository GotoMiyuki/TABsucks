import librosa
from typing import Dict, Any, List
from dataclasses import asdict
import torch
import torch.nn as nn
import numpy as np

class StemChordFormer(nn.Module):
    """
    针对 TABsucks 分轨架构优化的轻量级 Transformer 和弦识别模型。
    输入特征: 融合后的双流 CQT (Bass 低频特征 + Harmonics 中高频特征)
    """
    def __init__(self, input_dim=96, hidden_dim=128, num_classes=301, num_layers=2):
        super().__init__()
        
        # 1. 局部声学特征提取 (CNN 降维与平滑)
        self.frontend = nn.Sequential(
            nn.Conv1d(input_dim, hidden_dim, kernel_size=5, padding=2),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.MaxPool1d(2) # 时间轴下采样，加快序列处理
        )
        
        # 2. 全局和声逻辑推理 (Bi-directional Transformer)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden_dim, 
            nhead=4, 
            dim_feedforward=512, 
            dropout=0.1, 
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        # 3. 301类大词汇表分类头 (参考 ISMIR 2019 标准)
        self.classifier = nn.Linear(hidden_dim, num_classes)

    def forward(self, x):
        # x shape: (Batch, Freq_bins, Time)
        x = self.frontend(x)
        
        # 转换维度以适应 Transformer: (Batch, Time, Hidden)
        x = x.permute(0, 2, 1)
        
        # 获取上下文和声感知
        x = self.transformer(x)
        
        # 输出和弦概率分布 (Batch, Time, Num_Classes)
        logits = self.classifier(x)
        return logits
    
# 假设的基础类与数据结构导入
from src.plugins import Plugin
from src.kernel.core import ResourceController

class StemAwareChordAnalyzer(Plugin):
    """
    利用 BS-RoFormer 隔离后的纯净轨道，进行双流 CQT 提取并预测和弦。
    """
    
    @property
    def name(self) -> str:
        return "chord_analyzer_stem_aware"

    @property
    def version(self) -> str:
        return "1.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> Dict[str, Any]:
        """
        核心执行逻辑：向 RC 要缓存 -> 提取特征 -> 向 RC 要显存算力 -> 推理 -> 落库
        """
        print("[ChordAnalyzer] Starting stem-aware chord analysis...")
        
        # ==========================================
        # 1. 资源获取 (Resource Fetching)
        # 从共享内存中获取 BS-RoFormer 分离好的纯音轨数据
        # ==========================================
        sample_rate = 22050
        
        bass_audio = rc.get_buffer("bass")
        piano_audio = rc.get_buffer("piano")
        guitar_audio = rc.get_buffer("guitar")
        other_audio = rc.get_buffer("other")
        
        # 合并所有和声乐器，完全丢弃人声 (vocals) 和鼓 (drums)
        # 物理消灭经过音和打击乐瞬间噪音的干扰！
        harmonic_audio = piano_audio + guitar_audio + other_audio

        # ==========================================
        # 2. 纯净特征提取 (Dual-Stream CQT)
        # ==========================================
        # 提取 Bass 的低频 CQT (专注于 E1 到 E4，捕捉根音/转位)
        bass_cqt = self._extract_cqt(
            bass_audio, sr=sample_rate, fmin='E1', n_bins=36
        )
        
        # 提取 Harmonic 的中高频 CQT (专注于 C3 到 C7，捕捉和弦性质)
        harmonic_cqt = self._extract_cqt(
            harmonic_audio, sr=sample_rate, fmin='C3', n_bins=60
        )
        
        # 拼接特征维度 (36 + 60 = 96 维特征)
        # Shape: (96_bins, Time_frames)
        fused_features = np.vstack([bass_cqt, harmonic_cqt])
        fused_tensor = torch.tensor(fused_features).unsqueeze(0).float() # 加 Batch 维度

        # ==========================================
        # 3. 显存调度与模型推理 (VRAM Lock & Inference)
        # ==========================================
        # 向 ResourceController 申请加载和弦模型，此时如果分离模型还在显存，会被安全卸载
        model = rc.request_model("stem_chordformer", self._load_model)
        device = rc.get_current_device()
        
        fused_tensor = fused_tensor.to(device)
        
        with torch.no_grad():
            # 获取预测 logits
            logits = model(fused_tensor)
            # 取概率最大的和弦索引
            predictions = torch.argmax(logits, dim=-1).squeeze(0).cpu().numpy()

        # ==========================================
        # 4. 后处理与拍级对齐 (Post-processing)
        # ==========================================
        # 这里假设从 RC 或者前置插件中能拿到 BeatMap
        beat_timestamps = rc.get_metadata("beat_map") 
        
        chord_sequence = self._align_to_beats(predictions, beat_timestamps, sample_rate)
        
        # 主动释放重型资源
        rc.release_model("stem_chordformer")
        
        return {
            "status": "success",
            "chord_sequence": chord_sequence,
            "vocabulary_used": "ismir_301"
        }

    # ------------------ 私有辅助方法 ------------------

    def _load_model(self, model_path: str):
        """回调函数：供 ResourceController 实例化 PyTorch 模型"""
        model = StemChordFormer(input_dim=96, hidden_dim=128, num_classes=301, num_layers=2)
        # 实际代码中：model.load_state_dict(torch.load(model_path))
        model.eval()
        return model

    def _extract_cqt(self, audio: np.ndarray, sr: int, fmin: str, n_bins: int) -> np.ndarray:
        """提取常 Q 变换并转换为对数幅度谱"""
        hop_length = 2048
        fmin_hz = librosa.note_to_hz(fmin)
        
        # 计算 CQT
        C = librosa.cqt(y=audio, sr=sr, hop_length=hop_length, fmin=fmin_hz, n_bins=n_bins, bins_per_octave=12)
        
        # 转换为 dB (对数能量) 增强模型对微弱泛音的感知能力
        C_db = librosa.amplitude_to_db(np.abs(C), ref=np.max)
        
        # 归一化到 0-1 之间
        C_norm = (C_db - C_db.min()) / (C_db.max() - C_db.min() + 1e-8)
        return C_norm

    def _align_to_beats(self, frame_predictions: np.ndarray, beats: List[float], sr: int) -> List[Dict]:
        """将帧级别的和弦预测平滑对齐到用户的音乐节拍网格上"""
        # 实际开发中可以使用 Viterbi 解码或简单的多数投票机制 (Majority Vote)
        # 保证每一个小节/节拍内的和弦不会高频闪烁
        # 这里输出供 Canvas 渲染的 JSON 格式
        return[{"time": 0.0, "chord": "Cmaj7"}, {"time": 2.5, "chord": "G7"}]