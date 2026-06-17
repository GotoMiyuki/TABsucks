"""Bass Progression Plugin - 从 bass 轨检测低音进行序列。"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np

from src.analysis.chord import ChordEvent
from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


class BassRootPlugin(Plugin):
    """从 bass 轨检测低音进行的插件。"""

    @property
    def name(self) -> str:
        return "chord_bass_root"

    @property
    def version(self) -> str:
        return "2.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> dict:
        audio = rc.get_buffer("bass")
        sr = rc.get_metadata("sample_rate")
        beat_timestamps = rc.get_metadata("beat_timestamps")

        if beat_timestamps is not None and len(beat_timestamps) >= 2:
            progression = self._detect_progression(audio, sr, beat_timestamps)
            all_roots = [e.root for e in progression if e.root not in ("N", "X")]
            global_root = max(set(all_roots), key=all_roots.count) if all_roots else "N"
        else:
            global_root = self._detect_root(audio, sr)
            progression = []

        rc.set_metadata("bass_root", global_root)
        rc.set_metadata("bass_progression", progression)
        return {
            "status": "success",
            "bass_progression": [asdict(e) for e in progression],
            "root": global_root,
        }

    def _detect_progression(
        self, audio: np.ndarray, sr: int, beat_timestamps: list[float]
    ) -> list[ChordEvent]:
        """按 beat 分段检测低音进行。

        Args:
            audio: Bass 音频。
            sr: 采样率。
            beat_timestamps: 拍点时间戳列表。

        Returns:
            ChordEvent 列表（quality=""），相邻同 root 已合并。
        """
        import librosa

        fmin = librosa.note_to_hz("E1")
        fmax = librosa.note_to_hz("E4")

        f0, voiced_flag, _ = librosa.pyin(
            audio.astype(np.float64),
            fmin=fmin,
            fmax=fmax,
            sr=sr,
        )

        frame_times = librosa.times_like(f0, sr=sr)

        # 只保留 voiced 帧
        voiced_midi = librosa.hz_to_midi(f0[voiced_flag])
        voiced_midi = np.round(voiced_midi).astype(int)
        voiced_midi = voiced_midi[(voiced_midi >= 24) & (voiced_midi <= 72)]
        voiced_times = frame_times[voiced_flag]
        # 与 voiced_midi 等长（过滤后）
        mask = (np.round(librosa.hz_to_midi(f0[voiced_flag])).astype(int) >= 24) & (
            np.round(librosa.hz_to_midi(f0[voiced_flag])).astype(int) <= 72
        )
        voiced_times = voiced_times[mask]

        if len(voiced_midi) == 0:
            return []

        # 按 beat 区间分段
        beat_arr = np.asarray(beat_timestamps)
        # searchsorted: 每个 voiced frame 属于哪个 beat 区间
        segment_indices = np.searchsorted(beat_arr, voiced_times, side="right") - 1

        # 逐段取众数
        segments: list[tuple[float, float, int]] = []  # (start, end, midi)
        for i in range(len(beat_arr) - 1):
            seg_start = beat_arr[i]
            seg_end = beat_arr[i + 1]
            mask_seg = segment_indices == i
            pitches_in_seg = voiced_midi[mask_seg]
            if len(pitches_in_seg) == 0:
                continue
            mode_midi = int(np.bincount(pitches_in_seg).argmax())
            segments.append((seg_start, seg_end, mode_midi))

        if not segments:
            return []

        # 合并相邻同 root（MIDI % 12 相同）
        merged: list[tuple[float, float, int]] = [segments[0]]
        for seg_start, seg_end, midi in segments[1:]:
            prev_start, prev_end, prev_midi = merged[-1]
            if midi % 12 == prev_midi % 12:
                merged[-1] = (prev_start, seg_end, prev_midi)
            else:
                merged.append((seg_start, seg_end, midi))

        # 转换为 ChordEvent
        result: list[ChordEvent] = []
        for start, end, midi in merged:
            note_name = librosa.midi_to_note(midi, octave=False)
            result.append(ChordEvent(root=note_name, quality="", start=start, end=end))

        return result

    def _detect_root(self, audio: np.ndarray, sr: int) -> str:
        """使用 pyin 基频检测 bass 轨的全局根音。

        Returns:
            音名字符串（如 "A"），无声时返回 "N"。
        """
        import librosa

        fmin = librosa.note_to_hz("E1")
        fmax = librosa.note_to_hz("E4")

        f0, voiced_flag, _ = librosa.pyin(
            audio.astype(np.float64),
            fmin=fmin,
            fmax=fmax,
            sr=sr,
        )

        voiced_pitches = f0[voiced_flag]
        if len(voiced_pitches) == 0:
            return "N"

        midi_notes = librosa.hz_to_midi(voiced_pitches)
        rounded = np.round(midi_notes).astype(int)
        rounded = rounded[(rounded >= 24) & (rounded <= 72)]
        if len(rounded) == 0:
            return "N"

        most_common_midi = int(np.bincount(rounded).argmax())
        return librosa.midi_to_note(most_common_midi, octave=False)
