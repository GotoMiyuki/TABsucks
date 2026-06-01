"""
Bass Root Anchor Plugin - 只输出根音/低音，不识别复杂和弦性质
"""
import numpy as np
from src.plugins import Plugin
from src.kernel.core.resource_controller import ResourceController

class BassRootPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "chord_bass_root"

    def execute(self, rc: ResourceController, **kwargs):
        audio = rc.get_buffer("bass")
        sr = rc.get_metadata("sample_rate")
        # 实现轻量级根音检测（例如基于crepe或简单的音高追踪）
        root_note = self._detect_root(audio, sr)
        rc.set_metadata("bass_root", root_note)
        return {"status": "success", "root": root_note}

    def _detect_root(self, audio, sr):
        # 可暂时使用简单的低频能量峰值定位，或封装crepe
        pass