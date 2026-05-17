import os
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

# 这里的 import 你的路径是对的
from src.separation.separator import Separator, TrackId, SeparatorError

# ================= 辅助/假数据类 =================
class DummyAudioData:
    """模拟 src.audio.loader.AudioData，避免引入不必要的依赖"""
    def __init__(self, samples: np.ndarray, sample_rate: int):
        self.samples = samples
        self.sample_rate = sample_rate

# ================= 测试夹具 (Fixtures) =================
@pytest.fixture
def mock_audio_data():
    """生成 1秒长、采样率 44100、双声道的假音频数据"""
    sr = 44100
    n_samples = sr * 1
    # shape: (channels, samples) = (2, 44100)
    samples = np.random.rand(2, n_samples).astype(np.float32)
    return DummyAudioData(samples=samples, sample_rate=sr)

@pytest.fixture
def mock_separator():
    """创建一个 Separator 实例"""
    return Separator(model_name="dummy_model.ckpt")

# ================= 测试用例 =================


@patch("src.separation.separator.AudioSeparator")
@patch("src.separation.separator.sf")
def test_separate_success_exact_length(mock_sf, MockAudioSeparator, mock_separator, mock_audio_data):
    """测试常规分离：模型输出的音频长度与原音频完全一致"""
    
    # 1. 模拟引擎配置
    mock_engine = MagicMock()
    mock_engine.output_dir = "/tmp/fake_dir"
    # 模拟模型输出了 6 个文件
    mock_engine.separate.return_value = [
        "test_vocals.wav", "test_drums.wav", "test_bass.wav",
        "test_piano.wav", "test_guitar.wav", "test_other.wav"
    ]
    MockAudioSeparator.return_value = mock_engine
    
    # 2. 模拟 os.path.exists (让代码认为这6个文件都生成成功了)
    with patch("os.path.exists", return_value=True), \
         patch("os.remove"): # 防止真去删系统文件
        
        # 3. 模拟 soundfile.read 返回的数据 (双声道，长度一致)
        n_samples = mock_audio_data.samples.shape[1]
        fake_output_data = np.random.rand(n_samples, 2).astype(np.float32)
        mock_sf.read.return_value = (fake_output_data, 44100)
        
        # 4. 执行分离
        result = mock_separator.separate(mock_audio_data)
        
        # 5. 断言检查
        assert result.sample_rate == 44100
        assert result.vocals.shape == (n_samples, 2)
        assert result.drums.shape == (n_samples, 2)
        mock_engine.separate.assert_called_once()



@patch("src.separation.separator.AudioSeparator")
@patch("src.separation.separator.sf")
def test_separate_truncation_logic(mock_sf, MockAudioSeparator, mock_separator, mock_audio_data):
    """测试截断逻辑：模型输出的音频比原音频长 (包含 padding)"""
    mock_engine = MagicMock()
    mock_engine.output_dir = "/tmp/fake_dir"
    mock_engine.separate.return_value = ["test_vocals.wav"]
    MockAudioSeparator.return_value = mock_engine

    with patch("os.path.exists", return_value=True), patch("os.remove"):
        n_samples = mock_audio_data.samples.shape[1]
        # 模拟模型输出比原音频多了 100 个采样点
        fake_longer_data = np.random.rand(n_samples + 100, 2)
        mock_sf.read.return_value = (fake_longer_data, 44100)
        
        result = mock_separator.separate(mock_audio_data)
        
        # 断言：多出的 100 个采样点应该被成功截断
        assert result.vocals.shape == (n_samples, 2)



@patch("src.separation.separator.AudioSeparator")
@patch("src.separation.separator.sf")
def test_separate_padding_logic(mock_sf, MockAudioSeparator, mock_separator, mock_audio_data):
    """测试补零逻辑：模型输出的音频比原音频短"""
    mock_engine = MagicMock()
    mock_engine.output_dir = "/tmp/fake_dir"
    mock_engine.separate.return_value = ["test_vocals.wav"]
    MockAudioSeparator.return_value = mock_engine

    with patch("os.path.exists", return_value=True), patch("os.remove"):
        n_samples = mock_audio_data.samples.shape[1]
        # 模拟模型输出比原音频少了 50 个采样点
        fake_shorter_data = np.random.rand(n_samples - 50, 2)
        mock_sf.read.return_value = (fake_shorter_data, 44100)
        
        result = mock_separator.separate(mock_audio_data)
        
        # 断言：应该被自动补齐到 n_samples 长度
        assert result.vocals.shape == (n_samples, 2)
        # 断言：补齐的部分应该是 0
        assert np.all(result.vocals[-50:, :] == 0)



@patch("src.separation.separator.AudioSeparator")
@patch("src.separation.separator.sf")
def test_separate_missing_tracks(mock_sf, MockAudioSeparator, mock_separator, mock_audio_data):
    """测试防崩逻辑：模型少输出了某些轨道（如只输出了人声），其余轨道应为全零垫底"""
    mock_engine = MagicMock()
    mock_engine.output_dir = "/tmp/fake_dir"
    # 模型只吐出了一个 vocals
    mock_engine.separate.return_value = ["test_vocals.wav"]
    MockAudioSeparator.return_value = mock_engine

    with patch("os.path.exists", return_value=True), patch("os.remove"):
        n_samples = mock_audio_data.samples.shape[1]
        fake_data = np.random.rand(n_samples, 2)
        mock_sf.read.return_value = (fake_data, 44100)
        
        result = mock_separator.separate(mock_audio_data)
        
        # vocals 应该有数据
        assert not np.all(result.vocals == 0)
        # drums 因为没被模型输出，应该使用你代码中初始化的 np.zeros
        assert np.all(result.drums == 0)
        assert result.drums.shape == (n_samples, 2)


# ================= 真实推理测试 (Integration Test) =================

@pytest.mark.slow  # 打上 slow 标记，方便以后在命令行中跳过它
def test_separate_real_inference_e2e():
    """
    真正的端到端集成测试 (End-to-End Integration Test)。
    不做任何 Mock，真实加载模型并调用显卡/CPU进行推理。
    目的：
    1. 测试模型能否成功加载且不报错。
    2. 测试音频写入、读取的完整真实 I/O 链路。
    3. 测试模型输出的真实文件名是否能被你的代码正确解析。
    """
    # 1. 制造一段真实的、能发声的音频 (4秒钟的 440Hz 纯音正弦波，双声道)
    # 用纯音测试可以防止输入全 0 导致某些模型内部计算出现 NaN 报错
    sr = 44100
    duration = 4.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    audio_wave = 0.5 * np.sin(2 * np.pi * 440 * t) 
    
    # 转为双声道 shape: (2, 44100)
    samples = np.vstack((audio_wave, audio_wave)).astype(np.float32)
    real_audio_data = DummyAudioData(samples=samples, sample_rate=sr)
    
    # 2. 初始化真实的 Separator (使用默认模型 BS-Roformer)
    separator = Separator() 
    
    # 3. 执行真正的分离 (这里会极其耗时)
    print("\n[开始真实模型推理，请耐心等待...]")
    try:
        result = separator.separate(real_audio_data)
    except Exception as e:
        pytest.fail(f"真实推理过程中发生崩溃: {e}")
        
    # 4. 严苛的断言检查
    n_samples = samples.shape[1]
    
    # 检查基本属性
    assert result.sample_rate == sr
    
    # 检查 6 个轨道是否都正确生成，且维度被完美对齐
    tracks = ["vocals", "drums", "bass", "piano", "guitar", "other"]
    for track in tracks:
        track_data = getattr(result, track)
        
        # 断言 1: 数据类型必须是 NumPy 数组
        assert isinstance(track_data, np.ndarray), f"{track} 轨道不是 numpy 数组！"
        
        # 断言 2: 无论模型吐出来什么鬼样子，最后输出的 shape 必须和输入一致
        assert track_data.shape == (n_samples, 2), f"{track} 轨道维度错误，期望 {(n_samples, 2)}，实际 {track_data.shape}"
        
        # 断言 3: 检查 fallback 逻辑。如果你给了一段声音，经过模型处理，
        # 通常不太可能输出绝对纯净的 0（即便该轨道没有声音，也会有微弱的底噪浮点数）。
        # 如果某个轨道全为 0，大概率是你的文件名匹配逻辑失效，触发了 fallback。
        # 这是一个强力校验！
        assert not np.all(track_data == 0), f"警告：{track} 轨道数据全为0！可能是模型没有输出该轨道，或者文件名匹配规则写错了！"