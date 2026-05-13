from __future__ import annotations

import torch
import os
import tempfile


# ... 其他导入
#需要自行安装pytorch onnxruntime-gpu 还有ffmpeg
# ================= 硬件控制开关 =================
#########强制本地加载
os.environ["HF_HUB_OFFLINE"] = "0"  
#设置下载模型的镜像网站
#os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
# 如果想强制只用 CPU，取消下面这行的注释：
#os.environ["CUDA_VISIBLE_DEVICES"] = "-1" 

# 如果你的电脑有两张显卡，想强制它只用第二张（索引从0开始）：
# os.environ["CUDA_VISIBLE_DEVICES"] = "1"
# ================================================

"""音轨分离模块，使用 6 轨版 BS-RoFormer 模型。"""



import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf

# 引入核心分离引擎
from audio_separator.separator import Separator as AudioSeparator

# 避免循环导入时的类型提示报错
if TYPE_CHECKING:
    from src.audio.loader import AudioData

# 咱们项目首选的原生 6 轨 BS-RoFormer 模型配置
SUPPORTED_MODELS = [
    "BS-Roformer-SW.yaml", # audio-separator 库内置的 6 轨配置文件名称
]

class TrackId(Enum):
    """分离音轨类型枚举，保持内部状态统一"""
    VOCALS = "vocals"
    DRUMS = "drums"
    BASS = "bass"
    PIANO = "piano"
    GUITAR = "guitar"
    OTHER = "other"

@dataclass(frozen=True)
class SeparationResult:
    """
    音轨分离结果的数据类 (DataClass)。
    frozen=True 保证数据不可变，防止后续在 UI 或播放器里被意外修改。
    """
    vocals: np.ndarray
    drums: np.ndarray
    bass: np.ndarray
    piano: np.ndarray
    guitar: np.ndarray
    other: np.ndarray
    sample_rate: int

    def get_track(self, track_id: TrackId) -> np.ndarray:
        """通过枚举值动态获取对应的音轨数组"""
        return getattr(self, track_id.value)

class SeparatorError(Exception):
    """自定义分离器异常，方便在上层捕获并弹窗提示用户"""
    pass

class Separator:
    def __init__(self, model_name: str = "BS-Roformer-SW.ckpt") -> None:
        self.model_name = model_name
        self._separator_instance = None # 延迟加载实例，避免启动软件时卡死

    def _init_engine(self):
        """
        初始化底层推理引擎。
        只有在真正点击“开始分离”时才会触发，顺便设定好模型缓存和输出路径。
        """
        if self._separator_instance is None:
            self._separator_instance = AudioSeparator(
                model_file_dir="./models",        # 模型权重下载和存放的本地目录
                output_dir=tempfile.gettempdir(), # 分离后的中间文件临时存放在系统的 temp 目录
                output_format="WAV",              # 保证中间文件无损
            )
            try:
                # 如果本地没有模型，它会自动从 HuggingFace 下载
                self._separator_instance.load_model(self.model_name)
            except Exception as e:
                raise SeparatorError(f"模型加载失败: {e}")

    def separate(self, audio: AudioData) -> SeparationResult:
        """
        核心分离逻辑：
        内存(NumPy) -> 写入临时文件 -> 模型推理出6个临时文件 -> 读取6个文件回内存(NumPy) -> 清理垃圾
        """
        self._init_engine()
        sr = audio.sample_rate
        n_samples = audio.samples.shape[-1]
        
        # 1. 创建一个安全的临时输入文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_in:
            temp_in_path = temp_in.name
        
        try:
            # soundfile 写入时需要将 shape (channels, samples) 转置为 (samples, channels)
            sf.write(temp_in_path, audio.samples.T, sr)

            # 2. 调用模型，开始分离！
            # 6 轨模型跑完后，会返回一个包含了 6 个具体文件名的列表
            output_files = self._separator_instance.separate(temp_in_path)
            
            # 3. 初始化结果字典，用全零数组垫底
            # 万一模型少吐了某个轨，这里也有个静音轨道顶着，不会让后续和弦分析报错
            tracks_data = {
                "vocals": np.zeros((n_samples, 2)),
                "drums": np.zeros((n_samples, 2)),
                "bass": np.zeros((n_samples, 2)),
                "piano": np.zeros((n_samples, 2)),
                "guitar": np.zeros((n_samples, 2)),
                "other": np.zeros((n_samples, 2)),
            }

            # 4. 遍历提取模型吐出来的每个音频文件
            for filename in output_files:
                path = os.path.join(self._separator_instance.output_dir, filename)
                if not os.path.exists(path): 
                    continue # 文件不在就跳过，用上面初始化的全零数组
                
                # 读取分离后的音频数据
                data, _ = sf.read(path)
                
                ##############注释内容为压缩成单声道实现###############
                """# 如果模型输出的是立体声 (shape: samples, channels)，我们强转为单声道
                if data.ndim > 1:
                    data = data.mean(axis=1) 
                
                # 极其重要的一步：对齐数组长度！
                # AI 模型在做 STFT/ISTFT 变换时，因为窗口填充(padding)，
                # 吐出来的音频长度可能比原音频多出或者少几个采样点。
                # 必须强行对齐，否则后面 6 轨一起播放时会因为长度不同步而崩溃或报错。
                if len(data) > n_samples:
                    data = data[:n_samples] # 截断多余的尾巴
                elif len(data) < n_samples:
                    data = np.pad(data, (0, n_samples - len(data))) # 补零填满"""
                #####################接下里为双声道保留######################
                # 获取当前音频的采样点数 (第 0 个维度)
                current_samples = data.shape[0]
                
                # 【修改对齐逻辑，兼容 1D 和 2D 数组】
                if current_samples > n_samples:
                    # 截断超出部分（如果是 2D，要保留所有通道）
                    if data.ndim == 1:
                        data = data[:n_samples]
                    else:
                        data = data[:n_samples, :] 
                        
                elif current_samples < n_samples:
                    # 补零填满
                    pad_length = n_samples - current_samples
                    if data.ndim == 1:
                        data = np.pad(data, (0, pad_length))
                    else:
                        # 对于 2D 数组，只在时间轴(第0维)补零，通道轴(第1维)不补
                        data = np.pad(data, ((0, pad_length), (0, 0)))

                
                # 5. 根据文件名包含的关键词，智能归类到对应的音轨槽位里
                lower_name = filename.lower()
                if "vocals" in lower_name:
                    tracks_data["vocals"] = data
                elif "drums" in lower_name:
                    tracks_data["drums"] = data
                elif "bass" in lower_name:
                    tracks_data["bass"] = data
                elif "piano" in lower_name:
                    tracks_data["piano"] = data
                elif "guitar" in lower_name:
                    tracks_data["guitar"] = data
                else:
                    tracks_data["other"] = data

            # 6. 组装并返回最终的数据对象
            return SeparationResult(
                vocals=tracks_data["vocals"],
                drums=tracks_data["drums"],
                bass=tracks_data["bass"],
                piano=tracks_data["piano"],
                guitar=tracks_data["guitar"],
                other=tracks_data["other"],
                sample_rate=sr,
            )

        except Exception as e:
            raise SeparatorError(f"分离过程出错: {e}")
        
        finally:
            # 7. 环保清理：一定要把输入的临时文件删掉，防止撑爆用户的 C 盘
            if os.path.exists(temp_in_path):
                os.remove(temp_in_path)
            # 注意：咱们这里没删 output_files，audio-separator 内部默认策略可能会自己管，
            # 如果后面发现 temp 目录里垃圾文件太多，可以在这里补一个 os.remove(path) 循环。

    def separate_file(self, path: str | Path) -> SeparationResult:
        """提供一个快捷方法，允许直接传入文件路径进行分离，跳过手动创建 AudioData"""
        from src.audio.loader import load_audio
        audio = load_audio(path)
        return self.separate(audio)
    
