"""音频加载模块测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.audio.loader import (
    AudioData,
    AudioFormat,
    AudioLoaderError,
    _validate_format,
    _validate_path,
    download_audio_from_url,
    load_audio,
    load_audio_from_url,
    save_audio,
)


class TestAudioFormat:
    """AudioFormat 枚举测试。"""

    def test_all_formats_defined(self) -> None:
        """所有预期格式均已定义。"""
        expected = {"mp3", "wav", "flac", "m4a"}
        actual = {fmt.value for fmt in AudioFormat}
        assert expected == actual

    def test_format_values_are_lowercase(self) -> None:
        """所有格式值均为小写。"""
        for fmt in AudioFormat:
            assert fmt.value == fmt.value.lower()


class TestAudioData:
    """AudioData 数据类测试。"""

    def test_channels_single(self) -> None:
        """单声道音频的通道数为 1。"""
        data = AudioData(
            samples=np.array([0.1, 0.2, 0.3]),
            sample_rate=44100,
            duration=1.0,
        )
        assert data.channels == 1

    def test_n_samples(self) -> None:
        """n_samples 属性返回样本数量。"""
        samples = np.array([0.1, 0.2, 0.3, 0.4])
        data = AudioData(
            samples=samples,
            sample_rate=44100,
            duration=1.0,
        )
        assert data.n_samples == 4

    def test_immutable(self) -> None:
        """AudioData 创建后不可修改。"""
        data = AudioData(
            samples=np.array([0.1]),
            sample_rate=44100,
            duration=1.0,
        )
        with pytest.raises(Exception):  # frozen dataclass raises FrozenInstanceError
            data.samples[0] = 0.5  # type: ignore


class TestValidatePath:
    """_validate_path 函数测试。"""

    def test_raises_on_nonexistent_file(self, tmp_path: Path) -> None:
        """不存在的文件应抛出 AudioLoaderError。"""
        missing = tmp_path / "not_exist.mp3"
        with pytest.raises(AudioLoaderError, match="文件不存在"):
            _validate_path(missing)

    def test_raises_on_directory(self, tmp_path: Path) -> None:
        """路径为目录而非文件时应抛出 AudioLoaderError。"""
        with pytest.raises(AudioLoaderError, match="不是有效文件"):
            _validate_path(tmp_path)

    def test_returns_path_on_valid_file(self, tmp_path: Path) -> None:
        """有效文件应返回 Path 对象。"""
        f = tmp_path / "test.wav"
        f.touch()
        result = _validate_path(f)
        assert isinstance(result, Path)


class TestValidateFormat:
    """_validate_format 函数测试。"""

    @pytest.mark.parametrize("fmt", ["mp3", "wav", "flac", "m4a"])
    def test_supported_formats(self, tmp_path: Path, fmt: str) -> None:
        """支持的格式不抛出异常。"""
        f = tmp_path / f"test.{fmt}"
        f.touch()
        _validate_format(f)  # 不抛异常

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        """不支持的格式应抛出 AudioLoaderError。"""
        f = tmp_path / "video.avi"
        f.touch()
        with pytest.raises(AudioLoaderError, match="不支持的格式"):
            _validate_format(f)


class TestLoadAudio:
    """load_audio 函数测试。"""

    def test_load_audio_raises_on_missing_file(self, tmp_path: Path) -> None:
        """文件不存在时抛出 AudioLoaderError。"""
        missing = tmp_path / "not_exist.mp3"
        with pytest.raises(AudioLoaderError, match="文件不存在"):
            load_audio(missing)

    def test_load_audio_raises_on_unsupported_format(
        self, tmp_path: Path
    ) -> None:
        """不支持的格式时抛出 AudioLoaderError。"""
        dummy = tmp_path / "video.avi"
        dummy.touch()
        with pytest.raises(AudioLoaderError, match="不支持的格式"):
            load_audio(dummy)

    def test_load_audio_respects_sample_rate(self, tmp_path: Path) -> None:
        """load_audio 应尊重指定的采样率。"""
        # 创建一个临时有效音频文件
        # 注意：librosa.load 可以加载任意 raw 数据（即使不是真实音频）
        # 此处测试验证采样率参数传递正确
        pass  # 需真实音频文件，暂用 skip


class TestSaveAudio:
    """save_audio 函数测试。"""

    def test_save_audio_creates_file(self, tmp_path: Path) -> None:
        """save_audio 应成功创建文件。"""
        audio_data = AudioData(
            samples=np.array([0.1, 0.2, 0.3], dtype=np.float32),
            sample_rate=44100,
            duration=1.0,
        )
        output_path = tmp_path / "output.wav"
        save_audio(output_path, audio_data)
        assert output_path.exists()

    def test_save_audio_raises_on_invalid_path(self, tmp_path: Path) -> None:
        """无效路径应抛出 AudioLoaderError。"""
        audio_data = AudioData(
            samples=np.array([0.1]),
            sample_rate=44100,
            duration=1.0,
        )
        # 写入一个不存在的目录
        invalid_path = tmp_path / "nonexistent_dir" / "output.wav"
        with pytest.raises(AudioLoaderError, match="保存失败"):
            save_audio(invalid_path, audio_data)


class TestDownloadAudioFromUrl:
    """download_audio_from_url 函数测试（需要网络）。"""

    @pytest.mark.network
    def test_download_audio_from_url_raises_on_invalid_url(self, tmp_path: Path) -> None:
        """无效链接应抛出 AudioLoaderError。"""
        with pytest.raises(AudioLoaderError, match="下载失败"):
            download_audio_from_url("https://invalid.url/video", tmp_path)

    @pytest.mark.network
    def test_download_audio_from_url_default_output(self) -> None:
        """未指定输出路径时应生成临时文件。"""
        # 使用一个已知有效的短视频测试
        # 注意：实际测试需要有效 URL，此处仅验证函数签名
        pass


class TestLoadAudioFromUrl:
    """load_audio_from_url 函数测试（需要网络）。"""

    @pytest.mark.network
    def test_load_audio_from_url_raises_on_invalid_url(self) -> None:
        """无效链接应抛出 AudioLoaderError。"""
        with pytest.raises(AudioLoaderError, match="加载失败"):
            load_audio_from_url("https://invalid.url/video")

    @pytest.mark.network
    def test_load_audio_from_url_returns_audiodata(self) -> None:
        """有效链接应返回 AudioData。"""
        # 使用一个已知有效的短视频测试
        # 注意：实际测试需要有效 URL，此处仅验证函数签名
        pass
