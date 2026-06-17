"""分析编排引擎，串联节奏→节拍→分离→和弦→调性→精炼 流水线。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.analysis.beat import BeatInfo, BeatTracker, normalize_time_signature
from src.analysis.chord import ChordAnalyzer, ChordEvent
from src.analysis.key import KeyAnalysis, analyze_key
from src.analysis.refiner import refine
from src.analysis.rhythm import RhythmAnalyzer, RhythmInfo
from src.kernel.core.plugin_manager import PluginManager
from src.kernel.core.resource_controller import ResourceController

if TYPE_CHECKING:
    from src.plugins.separation.separator import SeparationResult


@dataclass
class AnalysisResult:
    """一次完整分析的结果汇总。"""

    rhythm: RhythmInfo | None = None
    beat_info: BeatInfo | None = None
    chord_events: dict[str, list[ChordEvent]] = field(default_factory=dict)
    bass_progression: list[ChordEvent] = field(default_factory=list)
    key_analysis: KeyAnalysis | None = None
    unified_chords: list[ChordEvent] = field(default_factory=list)
    separation_result: SeparationResult | None = None

    @property
    def bass_root(self) -> str:
        """从 bass_progression 推导全局根音（向后兼容）。"""
        if not self.bass_progression:
            return "N"
        roots = [e.root for e in self.bass_progression if e.root not in ("N", "X")]
        if not roots:
            return "N"
        return max(set(roots), key=roots.count)


class AnalysisEngineError(Exception):
    """分析引擎错误。"""

    pass


class AnalysisEngine:
    """分析编排引擎，串联节奏→节拍→分离→和弦→调性→精炼 流水线。

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

        # 2. 节拍网格生成（从 BPM 数学推算逐拍时间戳）
        self._report(progress_callback, "beat_grid", 0.0)
        self._result = result  # 暂存以便 _generate_beat_grid 访问 rhythm
        result.beat_info = self._generate_beat_grid(result.rhythm)
        self._report(progress_callback, "beat_grid", 1.0)

        # 3. 音轨分离
        self._report(progress_callback, "separation", 0.0)
        result.separation_result = self._run_separation()
        self._report(progress_callback, "separation", 1.0)

        # 4. 深度节奏分析（对分离后的鼓声轨等做精细节奏识别）
        if result.rhythm and result.rhythm.needs_deep_analysis:
            self._report(progress_callback, "deep_rhythm", 0.0)
            self._try_deep_rhythm()
            self._report(progress_callback, "deep_rhythm", 1.0)

        # 5. 低音进行检测
        self._report(progress_callback, "bass_progression", 0.0)
        result.bass_progression = self._run_bass_progression()
        self._report(progress_callback, "bass_progression", 1.0)

        # 6. 和弦识别（对每个目标 stem 执行）
        self._report(progress_callback, "chord", 0.0)
        chord_stems = self._get_available_chord_stems()
        for i, stem in enumerate(chord_stems):
            result.chord_events[stem] = self._run_chord(stem)
            self._report(
                progress_callback, "chord", (i + 1) / max(len(chord_stems), 1)
            )

        # 7. 调性分析
        self._report(progress_callback, "key_analysis", 0.0)
        result.key_analysis = self._run_key_analysis(result.bass_progression)
        self._report(progress_callback, "key_analysis", 1.0)

        # 8. 精炼（节拍对齐 → 多轨合并 → 转位标记）
        self._report(progress_callback, "refine", 0.0)
        beat_timestamps = self._rc.get_metadata("beat_timestamps") or []
        result.unified_chords = self._run_refine(
            result.chord_events, beat_timestamps, result.bass_progression
        )
        self._report(progress_callback, "refine", 1.0)

        # 9. 存储到 RC
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

    def _generate_beat_grid(self, rhythm_info: RhythmInfo | None) -> BeatInfo:
        """从 RhythmInfo 的 BPM 纯数学推算逐拍时间戳。

        Args:
            rhythm_info: 节奏分析结果。

        Returns:
            BeatInfo 实例，包含拍点事件列表。
        """
        if rhythm_info is None or rhythm_info.global_bpm is None or rhythm_info.global_bpm <= 0:
            self._rc.set_metadata("beat_timestamps", [])
            return BeatInfo()

        beat_duration = rhythm_info.beat_duration
        raw_audio = self._rc.get_buffer("raw")
        sr = self._rc.get_metadata("sample_rate") or 44100
        audio_duration = len(raw_audio) / sr

        # 从 t=0 到音频结束生成拍点
        beat_timestamps: list[float] = []
        t = 0.0
        while t < audio_duration:
            beat_timestamps.append(round(t, 6))
            t += beat_duration

        # 写入 RC 供下游插件使用
        self._rc.set_metadata("beat_timestamps", beat_timestamps)

        # 用 BeatTracker 构建 BeatInfo
        time_sig = (4, 4)
        if rhythm_info.time_signature:
            try:
                time_sig = normalize_time_signature(rhythm_info.time_signature)
            except Exception:
                pass

        tracker = BeatTracker(bpm=rhythm_info.global_bpm, time_signature=time_sig)
        return tracker.track(beat_timestamps)

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
        chord_dicts = (
            raw_result.get("data", raw_result) if isinstance(raw_result, dict) else raw_result
        )
        return self._chord_analyzer.analyze(chord_dicts)

    def _run_bass_progression(self) -> list[ChordEvent]:
        """调用 bass_root 插件获取低音进行序列。"""
        plugin = self._pm.get("chord_bass_root")
        if plugin is None:
            return []

        raw_result = self._pm.execute("chord_bass_root")
        progression_dicts = raw_result.get("bass_progression", [])
        if progression_dicts:
            return [ChordEvent(**d) for d in progression_dicts]
        return []

    def _run_key_analysis(self, bass_progression: list[ChordEvent]) -> KeyAnalysis | None:
        """从 bass progression 推断调性。"""
        if not bass_progression:
            return None
        return analyze_key(bass_progression)

    def _run_refine(
        self,
        chord_events: dict[str, list[ChordEvent]],
        beat_timestamps: list[float],
        bass_progression: list[ChordEvent],
    ) -> list[ChordEvent]:
        """执行精炼流水线：节拍对齐 → 多轨合并 → 转位标记。"""
        if not chord_events or not beat_timestamps:
            return []
        return refine(chord_events, beat_timestamps, bass_progression)

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
