"""Bass Root Anchor Plugin - 从 bass 轨检测根音，不识别复杂和弦性质。"""

import numpy as np

from src.plugins import Plugin
from src.kernel.core.resource_controller import ResourceController


class BassRootPlugin(Plugin):
    """从 bass 轨检测根音的轻量级插件。"""

    @property
    def name(self) -> str:
        return "chord_bass_root"

    @property
    def version(self) -> str:
        return "1.0.0"

    def execute(self, rc: ResourceController, **kwargs) -> dict:
        audio = rc.get_buffer("bass")
        sr = rc.get_metadata("sample_rate")
        root_note = self._detect_root(audio, sr)
        rc.set_metadata("bass_root", root_note)
        return {"status": "success", "root": root_note}

    def _detect_root(self, audio: np.ndarray, sr: int) -> str:
        """使用 pyin 基频检测 bass 轨的根音。

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

        # 过滤未发声帧
        voiced_pitches = f0[voiced_flag]
        if len(voiced_pitches) == 0:
            return "N"

        # Hz → MIDI 编号 → 四舍五入取最近半音 → 取众数
        midi_notes = librosa.hz_to_midi(voiced_pitches)
        rounded = np.round(midi_notes).astype(int)
        # 过滤掉超出合理 MIDI 范围的值（C1=24 到 C5=72 覆盖 bass 吉他全音域）
        rounded = rounded[(rounded >= 24) & (rounded <= 72)]
        if len(rounded) == 0:
            return "N"

        most_common_midi = int(np.bincount(rounded).argmax())
        return librosa.midi_to_note(most_common_midi, octave=False)
