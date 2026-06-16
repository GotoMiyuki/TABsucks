"""分析编排引擎，串联节奏→分离→和弦→（未来 Refiner）流水线。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.analysis.chord import ChordAnalyzer, ChordEvent
from src.analysis.rhythm import RhythmAnalyzer, RhythmInfo
from src.kernel.core.plugin_manager import PluginManager
from src.kernel.core.resource_controller import ResourceController

if TYPE_CHECKING:
    from src.plugins.separation.separator import SeparationResult


@dataclass
class AnalysisResult:
    """一次完整分析的结果汇总。"""

    rhythm: RhythmInfo | None = None
    chord_events: dict[str, list[ChordEvent]] = field(default_factory=dict)
    bass_root: str = "N"
    separation_result: SeparationResult | None = None


class AnalysisEngineError(Exception):
    """分析引擎错误。"""

    pass


class AnalysisEngine:
    """分析编排引擎，串联节奏→分离→和弦→bass_root 流水线。

    使用方式::

        rc = ResourceController()
        rc.set_buffer("raw", audio_array)
        rc.set_metadata("sample_rate", 22050)

        pm = PluginManager(rc)
        pm.register(FoundationRhythmPlugin())
        pm.register(ISMIR2019ChordPlugin())
        ...

        engine = AnalysisEngine(rc, pm)
        result = engine.run()
    """

    # 和弦识别默认在这些 stem 上执行
    CHORD_STEMS = ["piano", "guitar"]

    def __init__(self, rc: ResourceController, pm: PluginManager) -> None:
        self._rc = rc
        self._pm = pm
        self._rhythm_analyzer = RhythmAnalyzer()
        self._chord_analyzer = ChordAnalyzer()
        self._result: AnalysisResult | None = None

    @property
    def result(self) -> AnalysisResult | None:
        """最近一次 run() 的结果。"""
        return self._result

    def run(self, progress_callback=None) -> AnalysisResult:
        """执行完整分析流水线。

        Args:
            progress_callback: 可选回调 ``(step: str, progress: float)``
                用于报告进度。

        Returns:
            AnalysisResult 实例。
        """
        self._check_prerequisites()
        result = AnalysisResult()

        # 1. 节奏分析（前置侦察兵，基于原始混音的粗略 BPM/拍号）
        self._report(progress_callback, "rhythm", 0.0)
        result.rhythm = self._run_rhythm()
        self._report(progress_callback, "rhythm", 1.0)

        # 2. 音轨分离（deep_rhythm 需要分离后的鼓/乐器声轨）
        self._report(progress_callback, "separation", 0.0)
        result.separation_result = self._run_separation()
        self._report(progress_callback, "separation", 1.0)

        # 3. 深度节奏分析（对分离后的鼓声轨等做精细节奏识别）
        if result.rhythm and result.rhythm.needs_deep_analysis:
            self._report(progress_callback, "deep_rhythm", 0.0)
            self._try_deep_rhythm()
            self._report(progress_callback, "deep_rhythm", 1.0)

        # 4. 和弦识别（对每个目标 stem 执行）
        self._report(progress_callback, "chord", 0.0)
        chord_stems = self._get_available_chord_stems()
        for i, stem in enumerate(chord_stems):
            result.chord_events[stem] = self._run_chord(stem)
            self._report(
                progress_callback, "chord", (i + 1) / len(chord_stems)
            )

        # 5. bass 根音检测
        self._report(progress_callback, "bass_root", 0.0)
        result.bass_root = self._run_bass_root()
        self._report(progress_callback, "bass_root", 1.0)

        # 6. 存储到 RC
        self._rc.set_metadata("analysis_result", result)
        self._result = result
        return result

    # ------------------------------------------------------------------
    # 流水线各阶段
    # ------------------------------------------------------------------

    def _run_rhythm(self) -> RhythmInfo:
        """调用节奏插件并归一化结果。"""
        plugin = self._pm.get("rhythm_foundation")
        if plugin is None:
            return RhythmInfo()

        raw_result = self._pm.execute("rhythm_foundation")
        return self._rhythm_analyzer.analyze(raw_result)

    def _try_deep_rhythm(self) -> None:
        """尝试调用 deep_rhythm 插件（Phase C）。

        在分离之后执行，可访问鼓声轨等单独乐器数据。
        不存在时静默跳过。
        """
        # TODO: 等 deep_rhythm 插件实现后取消注释
        # deep_plugin = self._pm.get("rhythm_deep")
        # if deep_plugin is not None:
        #     deep_plugin.execute(self._rc)
        pass

    def _run_separation(self) -> object:
        """执行音轨分离并将结果写入 RC buffer。"""
        from src.audio.loader import AudioData
        from src.plugins.separation.separator import Separator, TrackId

        raw_audio = self._rc.get_buffer("raw")
        sr = self._rc.get_metadata("sample_rate") or 44100
        duration = len(raw_audio) / sr

        from src.plugins.separation.separator import Separator

        separator = Separator()
        audio_data = AudioData(samples=raw_audio, sample_rate=sr, duration=duration)
        result = separator.separate(audio_data)

        # 桥接：将分离结果写入 RC buffer，供后续插件使用
        for track_id in TrackId:
            self._rc.set_buffer(track_id.value, result.get_track(track_id))
        self._rc.set_metadata("separation_result", result)
        self._rc.set_metadata("sample_rate", result.sample_rate)

        return result

    def _run_chord(self, stem: str) -> list[ChordEvent]:
        """对指定 stem 执行和弦识别并归一化。"""
        chord_plugin_names = [
            "chord_chordnet_2e1d",
            "chord_btc_sl",
            "chord_ismir2019",
            "chord_analyzer_stem_aware",
        ]

        raw_result = None
        for plugin_name in chord_plugin_names:
            plugin = self._pm.get(plugin_name)
            if plugin is not None:
                raw_result = self._pm.execute(plugin_name, stem_name=stem)
                break

        if raw_result is None:
            return []

        # 兼容两种返回格式：{"data": [...]} 或直接 [...]
        chord_dicts = raw_result.get("data", raw_result) if isinstance(raw_result, dict) else raw_result
        return self._chord_analyzer.analyze(chord_dicts)

    def _run_bass_root(self) -> str:
        """调用 bass_root 插件检测根音。"""
        plugin = self._pm.get("chord_bass_root")
        if plugin is None:
            return "N"

        raw_result = self._pm.execute("chord_bass_root")
        return raw_result.get("root", "N")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _check_prerequisites(self) -> None:
        """检查 RC 中是否有必要的前置数据。"""
        if self._rc.get_metadata("sample_rate") is None:
            try:
                self._rc.get_buffer("raw")
            except Exception:
                raise AnalysisEngineError(
                    "RC 中缺少 'raw' buffer，请先加载音频数据。"
                )

    def _get_available_chord_stems(self) -> list[str]:
        """返回已有 buffer 的和弦分析目标 stem 列表。"""
        available = []
        for stem in self.CHORD_STEMS:
            try:
                self._rc.get_buffer(stem)
                available.append(stem)
            except Exception:
                pass
        return available

    @staticmethod
    def _report(callback, step: str, progress: float) -> None:
        if callback is not None:
            callback(step, progress)
