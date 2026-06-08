# 开发日志：可视化模块（FR-06）

**日期：** 2026-05-30
**涉及模块：**
1. `src/visualizer/waveform.py`
2. `src/visualizer/beat.py`
3. `src/visualizer/chord.py`
4. `src/visualizer/export.py`
5. `src/visualizer/__init__.py`
**依赖项：** `numpy`

---

## 一、模块概述

可视化模块负责将音频数据、节拍信息与和弦事件转换为前端可直接渲染的标准化数据结构。

### 核心设计原则

- **比例解耦**：所有时间/位置值以 `*Proportion`（0~1 比例）输出，前端自行通过 `pps = canvasWidth / duration` 计算像素位置
- **Backend-only 物理量**：不含 `pixels_per_second` 等与视图相关的值
- **不可变数据**：所有可视化数据类为 `frozen=True` dataclass

---

## 二、架构设计

### 2.1 模块结构

```mermaid
flowchart TB
    subgraph "输入层"
        AD[AudioData<br/>samples · sample_rate · duration]
        BI[BeatInfo<br/>bpm · beat_events]
        CE[ChordEvents<br/>root · quality · start · end]
    end

    subgraph "处理层"
        CW[compute_waveform()]
        CB[build_beat_markers()]
        CC[build_chord_labels()]
    end

    subgraph "数据层"
        WD[WaveformData<br/>frozen dataclass]
        BD[BeatMarkerData<br/>frozen dataclass]
        CD[ChordLabelData<br/>frozen dataclass]
    end

    subgraph "导出层"
        EXP[export_visualization_json()]
    end

    AD --> CW --> WD
    BI --> CB --> BD
    CE --> CC --> CD
    WD --> EXP
    BD --> EXP
    CD --> EXP
    EXP --> JSON[(JSON dict<br/>或文件)]
```

### 2.2 比例映射原理

```
前端渲染波形（以 React/Canvas 为例）：

canvasWidth = 800px
duration = 60s
pps = 800 / 60 ≈ 13.3 px/s

第 N 帧的像素 x 坐标：
x = peaks[N] * canvasHeight

第 t 秒的节拍像素 x 坐标：
x = (t / 60) * 800
```

所有 `*Proportion` 值本质上是归一化的时间/位置因子，前端负责乘以实际像素尺寸。

---

## 三、核心数据结构

### 3.1 WaveformData

```python
@dataclass(frozen=True)
class WaveformData:
    peaks: np.ndarray          # shape (num_frames,)，归一化到 [0, 1]
    duration: float             # 秒
    sample_rate: int            # Hz
    frame_interval: float       # 秒/帧 = duration / num_frames

    def time_at_frame(n)        # t_n = n * frame_interval
    def frame_at_time(t)        # n = int(t / frame_interval)，clamp 到最后帧
    def proportion_at_frame(n)  # n / (total_frames - 1)
    def time_proportion(t)      # t / duration，用 max(duration, 0.001) 防零除
    def to_dict() → dict        # 序列化（不含 pps）
```

**向量化计算**（无 Python 循环）：

```python
# 将音频切分为 num_frames 段，每段取最大值
n_frames = min(num_frames, len(samples))
frames_per_chunk = len(samples) // n_frames
reshaped = samples[:n_frames * frames_per_chunk].reshape(n_frames, frames_per_chunk)
peaks = np.max(np.abs(reshaped), axis=1)
# 归一化到 [0, 1]
peaks = peaks / (np.max(peaks) + 1e-9)
```

### 3.2 BeatMarkerData

```python
@dataclass(frozen=True)
class BeatMarkerData:
    beats: list[BeatEvent]
    duration: float

    def to_dict() → list[dict]:
        # 每个 dict 包含：
        # time           - 秒（物理时间）
        # measure        - 小节编号（从 1 开始）
        # beatInMeasure  - 拍号（1~beats_per_measure）
        # isDownbeat     - 是否小节第一拍
        # timeProportion - time / duration（比例）
```

### 3.3 ChordLabelData

```python
@dataclass(frozen=True)
class ChordLabelData:
    chords: list[ChordEvent]
    duration: float

    def to_dict() → list[dict]:
        # 每个 dict 包含：
        # start               - 秒
        # end                 - 秒
        # duration            - end - start（秒）
        # name                - root + quality（如 "Cm7"）
        # root                - 根音
        # quality             - 音色
        # startProportion     - start / duration
        # durationProportion  - (end - start) / duration
```

---

## 四、函数详解

### 4.1 compute_waveform(audio, num_frames=2000)

| 参数 | 说明 |
|------|------|
| `audio` | Duck-typed 对象，具有 `samples / sample_rate / duration` |
| `num_frames` | 输出帧数，默认 2000 |

**流程**：

```python
# 1. 混音：2D → 1D
if audio.samples.ndim == 2:
    samples = np.mean(audio.samples, axis=0)

# 2. 截断到 n_frames
n = min(num_frames, len(samples))
samples = samples[:n * frames_per_chunk]

# 3. reshape + max（向量化）
reshaped = samples.reshape(n, frames_per_chunk)
peaks = np.max(np.abs(reshaped), axis=1)

# 4. 归一化
peak_max = np.max(peaks)
peaks = peaks / (peak_max + 1e-9)  # 静音防零除
```

### 4.2 build_beat_markers(beat_info, duration=None)

- `duration=None` 时，从 `last_beat_number / beats_per_measure * 60/bpm` 推断
- 每个 `BeatEvent` 的小节号：`measure = (beat_number - 1) // beats_per_measure + 1`
- `isDownbeat = (beat_number - 1) % beats_per_measure == 0`

### 4.3 build_chord_labels(chords, duration=None)

- `duration=None` 时，从最后一个和弦的 `end` 推断
- `startProportion = start / max(duration, 0.001)`（防零除）
- `durationProportion = (end - start) / max(duration, 0.001)`

### 4.4 export_visualization_json(audio, beat_info=None, chord_events=None, num_waveform_frames=2000, output_path=None)

统一导出接口，输出结构：

```python
{
    "waveform": WaveformData.to_dict(),      # 必有
    "beats": BeatMarkerData.to_dict() | None,  # 可选
    "chords": ChordLabelData.to_dict() | None,  # 可选
    "metadata": {
        "duration": float,
        "sampleRate": int,
        "hasBeatData": bool,
        "hasChordData": bool,
        "exportedAt": str,  # ISO timestamp
    }
}
```

---

## 五、单元测试

### 5.1 测试文件

| 文件 | 测试数 |
|------|--------|
| `tests/unit/test_visualizer_waveform.py` | 16 |
| `tests/unit/test_visualizer_beat.py` | 8 |
| `tests/unit/test_visualizer_chord.py` | 12 |
| `tests/unit/test_visualizer_export.py` | 17 |
| **合计** | **53** |

### 5.2 关键测试场景

| 测试 | 验证点 |
|------|--------|
| `test_to_dict_no_pixels_per_second` | `to_dict()` 输出不含 `pps` / `pixelsPerSecond` |
| `test_stereo_input_mixed_to_mono` | 立体声自动混音 |
| `test_silent_audio_produces_zero_peaks` | 静音防零除不报错 |
| `test_duration_zero_guarded` | `max(duration, 0.001)` 防零除 |
| `test_short_audio_reduces_num_frames` | 音频短于 num_frames 时自动缩减 |
| `test_beat_calculation` | 小节号跨小节递增（beat 1-4→小节1，beat 5→小节2） |
| `test_chord_proportions` | `startProportion` / `durationProportion` 正确 |
| `test_json_roundtrip_full_output` | `dumps → loads` 数据完全一致 |

---

## 六、设计决策记录

### 6.1 为什么 `to_dict()` 不含 `pixels_per_second`？

**决策**：像素位置是前端视图相关的数据，不应由后端计算。

后端输出 `timeProportion = time / duration`，前端负责 `x = timeProportion * canvasWidth`。这确保同一后端数据可以在不同宽度的视图中复用，也支持响应式布局。

### 6.2 为什么用 `max(duration, 0.001)` 而不是 `if duration == 0`？

**决策**：`if` 分支在极少数边界情况下会改变业务逻辑（边界情况时的行为与正常时不同）。而 `max` 是一个平滑的防零除策略，行为一致性更好，且开销极低。

### 6.3 为什么 `compute_waveform` 默认 2000 帧？

**决策**：2000 帧兼顾了精度和性能。

- 2000 帧 × 2 Bytes（float32）≈ 16KB，数据量小，适合网络传输
- 2000 足够在典型屏幕宽度（800~1920px）上渲染流畅波形
- 若前端需要更高精度，可通过参数调高

### 6.4 为什么不直接渲染图像，而是输出 JSON？

**决策**：架构解耦。

JSON 输出让前端决定渲染方式（Canvas / SVG / WebGL），同一数据可在多个视图中复用，也便于调试（可直接打印查看）。

---

## 七、与 FR-05 的关系

| FR-05 | FR-06 |
|-------|-------|
| `AudioPlayer.load(audio_data)` | `compute_waveform(audio_data)` |
| 共享同一个 `AudioData` 对象 | 输出 `waveform` JSON |
| 播放时通过 `get_progress()` 轮询 | 播放可视化仍为静态导出（未来在 Workshop 层联动） |

当前两者均独立接收 `AudioData`，在 `MusicWorkshop` 层面建立关联后，可实现播放时实时更新可视化标记。

---

## 八、文件清单

```
src/visualizer/waveform.py      # 波形数据生成
src/visualizer/beat.py          # 节拍标记生成
src/visualizer/chord.py         # 和弦标签生成
src/visualizer/export.py        # 统一导出
src/visualizer/__init__.py      # 模块导出
tests/unit/test_visualizer_waveform.py  # 波形测试（16 tests）
tests/unit/test_visualizer_beat.py      # 节拍测试（8 tests）
tests/unit/test_visualizer_chord.py     # 和弦测试（12 tests）
tests/unit/test_visualizer_export.py    # 导出测试（17 tests）
```
