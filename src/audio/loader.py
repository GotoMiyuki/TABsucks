"""音频加载模块，支持多格式输入。

支持的格式：MP3, WAV, FLAC, M4A

功能划分：
  - load_audio: 本地文件加载（单声道 + 可重采样）
  - load_audio_multi_channel: 本地文件加载（保留多通道原始格式）
  - save_audio: 音频保存到本地文件
  - download_audio_from_url: 从 YouTube/Bilibili 下载音频
  - load_audio_from_url: 从 YouTube/Bilibili 直接加载到内存

注意：URL 下载功能依赖 ffmpeg，会在首次调用时通过 static-ffmpeg 自动检测或下载。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import librosa
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _ensure_ffmpeg() -> None:
    """确保 ffmpeg 可用，不存在则自动下载。

    依赖 static-ffmpeg，会在首次调用时将 ffmpeg 路径加入系统 PATH。
    yt-dlp 内部会调用 ffmpeg 进行音轨提取和格式转换。

    调用时机：仅在 download_audio_from_url / load_audio_from_url 调用前触发，
    本地文件加载（librosa 直接解码）不需要 ffmpeg。
    """
    import static_ffmpeg

    static_ffmpeg.add_paths()


def _validate_path(path: str | Path) -> Path:
    """验证路径是否存在且为文件。

    Args:
        path: 待验证的路径（字符串或 Path 对象）。

    Returns:
        验证通过的 Path 对象。

    Raises:
        AudioLoaderError: 路径不存在或不是文件。
    """
    p = Path(path)
    if not p.exists():
        raise AudioLoaderError(f"文件不存在: {p}")
    if not p.is_file():
        raise AudioLoaderError(f"不是有效文件: {p}")
    return p


def _validate_format(path: Path) -> None:
    """验证文件扩展名是否为支持的音频格式。

    Args:
        path: 待验证的 Path 对象。

    Raises:
        AudioLoaderError: 文件格式不支持。
    """
    suffix = path.suffix.lower().lstrip(".")
    supported = {fmt.value for fmt in AudioFormat}
    if suffix not in supported:
        raise AudioLoaderError(f"不支持的格式: .{suffix}，支持: {', '.join(sorted(supported))}")


# ---------------------------------------------------------------------------
# 数据结构与枚举
# ---------------------------------------------------------------------------


class AudioFormat(Enum):
    """支持的音频格式枚举。

    注意：此处定义的是文件扩展名，librosa 和 soundfile 内部会依据
    扩展名调用对应的解码器，理论上支持更多格式（如 ogg），但我们
    在这里限定为最常用的四种。
    """

    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    M4A = "m4a"


@dataclass(frozen=True)
class AudioData:
    """不可变的音频数据结构。

    所有音频数据加载后都封装为 AudioData 返回，确保线程安全
    和引用透明。frozen=True 防止意外修改。

    Attributes:
        samples: 单声道音频样本，形状为 (n_samples,)，数据类型为 float32。
        sample_rate: 采样率(Hz)，如 44100、22050。
        duration: 时长（秒），由 samples 数量除以采样率计算得出。
    """

    samples: np.ndarray
    sample_rate: int
    duration: float

    @property
    def channels(self) -> int:
        """音频通道数，等于 samples 的维度数。
        1 维 → 单声道，2 维 → 多声道（如立体声）。
        """
        return self.samples.ndim

    @property
    def n_samples(self) -> int:
        """样本数量，等于 samples 最后一维的长度。

        对于单声道 (n_samples,)，直接返回长度；
        对于多声道 (channels, n_samples)，返回 n_samples。
        """
        return self.samples.shape[-1]


class AudioLoaderError(Exception):
    """音频加载/保存/下载操作失败时抛出的异常。

    统一异常类型方便调用方统一捕获。
    """


class IAudioLoader(Protocol):
    """音频加载器抽象接口，定义统一的加载行为。

    供插件系统（PluginManager）使用，任何实现此接口的加载器
    都可以被统一调用，便于扩展（如未来支持远程 URL 加载器、
    流式加载器等）。
    """

    def load(self, path: str | Path) -> AudioData:
        """加载音频文件并返回 AudioData。"""
        ...


# ---------------------------------------------------------------------------
# 本地文件加载
# ---------------------------------------------------------------------------


def load_audio(path: str | Path, sr: int = 44100) -> AudioData:
    """加载本地音频文件并转为单声道重采样后的 numpy 数组。

    工作流程：
      1. 验证路径存在且格式支持
      2. 使用 librosa.load 读取音频，mono=True 混为单声道
      3. 若指定 sr != None，librosa 会自动重采样到目标采样率
      4. 计算时长并封装为 AudioData 返回

    适用场景：音轨分离、和弦/节奏分析等需要统一格式的下游任务。
    librosa 的重采样使用高质量的 SRC（采样率转换）算法。

    Args:
        path: 音频文件路径，支持相对路径和绝对路径。
        sr: 目标采样率（Hz），默认 44100Hz。
             - 设为 None 则保留原始采样率（不重采样）
             - 设为具体数值则 librosa 会自动转换

    Returns:
        AudioData 对象，包含：
          - samples: 单声道 float32 数组，形状 (n_samples,)
          - sample_rate: 最终采样率
          - duration: 时长（秒）

    Raises:
        AudioLoaderError: 文件不存在、格式不支持或 librosa 加载失败。
    """
    path = _validate_path(path)
    _validate_format(path)

    try:
        # librosa.load 返回 (y, sr)
        # - y: float32 ndarray，范围 [-1, 1]
        # - sr: 实际加载的采样率（可能与请求的不同）
        y, loaded_sr = librosa.load(path, sr=sr, mono=True)
        duration = len(y) / loaded_sr
        return AudioData(samples=y, sample_rate=loaded_sr, duration=duration)
    except Exception as e:
        raise AudioLoaderError(f"加载失败: {e}") from e


def load_audio_multi_channel(path: str | Path) -> AudioData:
    """加载本地音频文件并保留多通道原始采样率。

    与 load_audio 的区别：
      - 不转单声道（mono=False），保留原始声道数
      - 不重采样（sr=None），保留原始采样率
      - samples 形状为 (channels, n_samples) 而非 (n_samples,)

    适用场景：多轨播放、空间音频处理、需要分别获取左右声道等。

    Args:
        path: 音频文件路径。

    Returns:
        AudioData 对象，samples 形状为 (channels, n_samples)。

    Raises:
        AudioLoaderError: 文件不存在或加载失败。
    """
    path = _validate_path(path)
    _validate_format(path)

    try:
        # mono=False: 不转单声道，保留原始声道信息
        # sr=None: 保留原始采样率
        y, sr = librosa.load(path, sr=None, mono=False)
        # librosa 对单声道音频返回 (n_samples,)，需要扩展为 (1, n_samples)
        if y.ndim == 1:
            y = y[np.newaxis, :]
        duration = y.shape[-1] / sr
        return AudioData(samples=y, sample_rate=sr, duration=duration)
    except Exception as e:
        raise AudioLoaderError(f"加载失败: {e}") from e


def save_audio(path: str | Path, data: AudioData) -> None:
    """将 AudioData 保存为音频文件。

    使用 soundfile 写入，文件格式由 path 的扩展名决定。
    soundfile 会根据扩展名自动选择对应的编解码器。

    写入格式说明：
      - WAV (.wav): PCM 16/32bit 或 float
      - FLAC (.flac): 无损压缩
      - MP3 (.mp3): via soundfile (需要 ffmpeg)
      - M4A (.m4a): via soundfile (需要 ffmpeg)

    注意：
      - 如果 data.samples 是 (n_samples,) 单声道，直接写入
      - 如果是 (channels, n_samples) 多声道，soundfile 会正确处理

    Args:
        path: 输出路径（扩展名决定格式，如 "output.wav"）
        data: AudioData 对象

    Raises:
        AudioLoaderError: 保存失败（目录不存在、磁盘满、权限问题等）
    """
    import soundfile as sf

    path = Path(path)
    try:
        # soundfile.write 要求 samples 为 (n_samples, channels) 或 (n_samples,)
        # AudioData.samples 形状为 (n_samples,) 或 (channels, n_samples)
        # 多声道情况下需要转置 .T
        sf.write(path, data.samples.T, data.sample_rate)
    except Exception as e:
        raise AudioLoaderError(f"保存失败: {e}") from e


# ---------------------------------------------------------------------------
# 环境配置
# ---------------------------------------------------------------------------


def get_api_key() -> str:
    """从环境变量读取 API Key。

    用于需要付费 API 的场景（如云端 ASR、歌词对齐等）。
    当前音频加载模块本身不依赖此函数，预留接口给未来插件。

    Returns:
        API Key 字符串。

    Raises:
        RuntimeError: 环境变量 AUDIO_API_KEY 未设置。
    """
    key = os.environ.get("AUDIO_API_KEY")
    if not key:
        raise RuntimeError("AUDIO_API_KEY 环境变量未设置")
    return key


# ---------------------------------------------------------------------------
# URL 下载与加载
# ---------------------------------------------------------------------------


def download_audio_from_url(
    url: str,
    output_path: str | Path | None = None,
    format: str = "mp3",
    progress: bool = False,
) -> Path:
    """从网络链接下载音频（YouTube/Bilibili）。

    工作流程：
      1. _ensure_ffmpeg() 确保 ffmpeg 可用（自动下载如缺失）
      2. 构造 yt-dlp 配置，指定输出路径和格式
      3. yt-dlp 下载视频并调用 ffmpeg 提取音轨
      4. 验证文件生成并返回路径

    yt-dlp 与 ffmpeg 的配合：
      - yt-dlp 负责从 YouTube/Bilibili 获取视频流 URL
      - ffmpeg 负责将音轨从视频容器中提取并转码为目标格式
      - 两者配合缺一不可，static-ffmpeg 确保 ffmpeg 自动可用

    Args:
        url: 视频链接，支持 YouTube 和 Bilibili。
        output_path: 输出文件路径，默认在当前目录生成 temp_audio_{pid}.{format}。
        format: 输出格式（mp3/m4a/flac/wav），默认 mp3。
        progress: 是否显示下载进度，默认 False。

    Returns:
        下载的音频文件路径（Path 对象）。

    Raises:
        AudioLoaderError: 下载失败（网络问题、视频不存在、ffmpeg 不可用等）。

    Examples:
        >>> path = download_audio_from_url("https://www.youtube.com/watch?v=...")
        >>> path = download_audio_from_url("https://www.bilibili.com/video/BV...", format="m4a")
    """
    import yt_dlp

    _ensure_ffmpeg()  # 确保 yt-dlp 可用的 ffmpeg 存在

    # 默认路径：当前目录 temp_audio_{进程id}.{format}
    if output_path is None:
        output_path = Path(f"temp_audio_{os.getpid()}.{format}")
    output_path = Path(output_path)

    # yt-dlp 配置
    ydl_opts: dict = {
        "format": "bestaudio/best",  # 选择最佳音频流
        "outtmpl": str(output_path.with_suffix("")),  # 输出模板（不含扩展名）
        "quiet": not progress,  # 静默模式控制
    }

    # 非 webm 格式需要 FFmpegExtractAudio 后处理器进行转码
    if format != "webm":
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": format,
            }
        ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        raise AudioLoaderError(f"下载失败: {e}") from e

    # yt-dlp 实际输出的文件扩展名可能与 format 不完全一致，
    # 这里用 format 拼接扩展名来定位输出文件
    actual_path = output_path.with_suffix(f".{format}")
    if not actual_path.exists():
        raise AudioLoaderError(f"音频文件未生成: {actual_path}")
    return actual_path


def load_audio_from_url(url: str, sr: int = 44100) -> AudioData:
    """从网络链接直接加载音频到内存（YouTube/Bilibili）。

    与 download_audio_from_url 的区别：
      - 不保存到本地文件，所有操作在内存中完成
      - 内部仍需要yt-dlp下载到临时目录，但加载完成后自动清理
      - 返回 AudioData 后临时文件立即删除

    工作流程：
      1. _ensure_ffmpeg() 确保 ffmpeg 可用
      2. 用 yt-dlp 提取视频信息（不下载）
      3. 创建临时目录，配置 yt-dlp 下载音频（转为 WAV）到该目录
      4. 调用 load_audio 加载临时文件为 AudioData
      5. 临时目录在退出 with 块时自动删除

    注意：yt-dlp 需要两次初始化（extract_info 和 download）是因为
    download 模式会覆盖 extract_info 的缓存，必须分开执行。

    Args:
        url: 视频链接（YouTube 或 Bilibili）。
        sr: 目标采样率（Hz），默认 44100Hz，传递给内部的 load_audio。

    Returns:
        AudioData 对象，包含音频样本、采样率和时长。

    Raises:
        AudioLoaderError: 下载或加载失败（网络问题、视频不存在等）。

    Examples:
        >>> audio = load_audio_from_url("https://www.youtube.com/watch?v=...")
        >>> print(audio.duration)
        245.3
    """
    import yt_dlp
    import tempfile

    _ensure_ffmpeg()

    # 第一次调用：仅获取视频信息（不下载），用于验证 URL 有效性
    ydl_opts: dict = {
        "format": "bestaudio/best",
        "quiet": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise AudioLoaderError("无法获取视频信息")

        # 第二次调用：在临时目录中下载音频文件
        with tempfile.TemporaryDirectory() as tmpdir:
            # 配置下载选项
            ydl_opts["outtmpl"] = str(Path(tmpdir) / "audio")
            ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]
            with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                ydl2.download([url])

            # 查找生成的 WAV 文件并加载
            audio_files = list(Path(tmpdir).glob("audio.wav"))
            if not audio_files:
                raise AudioLoaderError("音频文件未生成")
            return load_audio(audio_files[0], sr=sr)
    except AudioLoaderError:
        raise
    except Exception as e:
        raise AudioLoaderError(f"加载失败: {e}") from e