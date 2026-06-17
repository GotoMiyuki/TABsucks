"""和弦后处理精炼器（Refiner）。

独立模块（非插件），由 AnalysisEngine 直接调用。
功能：节拍对齐 → 多轨合并 → 转位标记。
"""

from __future__ import annotations

from src.analysis.chord import ChordEvent


# ---------------------------------------------------------------------------
# Step 1: BeatSync — 节拍对齐 + 去抖动
# ---------------------------------------------------------------------------


def snap_to_beats(
    events: list[ChordEvent],
    beat_timestamps: list[float],
) -> list[ChordEvent]:
    """将和弦事件的 start/end snap 到最近的节拍边界，合并相邻相同和弦。

    Args:
        events: 原始和弦事件列表。
        beat_timestamps: 拍点时间戳列表（秒）。

    Returns:
        节拍对齐且去抖动后的和弦事件列表。
    """
    if not events or not beat_timestamps:
        return events

    snapped: list[ChordEvent] = []
    for event in events:
        new_start = _nearest_beat(event.start, beat_timestamps)
        new_end = _nearest_beat(event.end, beat_timestamps)
        if new_end <= new_start:
            # 确保 end > start：取 start 对应 beat 的下一个 beat
            try:
                idx = beat_timestamps.index(new_start)
                next_idx = min(idx + 1, len(beat_timestamps) - 1)
                new_end = beat_timestamps[next_idx]
            except ValueError:
                new_end = new_start + 0.5
            if new_end <= new_start:
                new_end = new_start + 0.5
        snapped.append(
            ChordEvent(
                root=event.root,
                quality=event.quality,
                start=new_start,
                end=new_end,
            )
        )

    return _merge_adjacent(snapped)


def _nearest_beat(time: float, beat_times: list[float]) -> float:
    """找到离给定时间最近的节拍边界。"""
    best = beat_times[0]
    best_dist = abs(time - best)
    for bt in beat_times:
        dist = abs(time - bt)
        if dist < best_dist:
            best_dist = dist
            best = bt
    return best


def _merge_adjacent(events: list[ChordEvent]) -> list[ChordEvent]:
    """合并相邻且 root+quality 相同的事件。"""
    if not events:
        return []
    merged = [events[0]]
    for ev in events[1:]:
        prev = merged[-1]
        if ev.root == prev.root and ev.quality == prev.quality:
            merged[-1] = ChordEvent(
                root=prev.root,
                quality=prev.quality,
                start=prev.start,
                end=ev.end,
            )
        else:
            merged.append(ev)
    return merged


# ---------------------------------------------------------------------------
# Step 3: MultiStemMerge — 多轨合并
# ---------------------------------------------------------------------------


def merge_stem_chords(
    piano_events: list[ChordEvent],
    guitar_events: list[ChordEvent],
) -> list[ChordEvent]:
    """合并 piano + guitar 和弦为统一视图。

    策略：
    - root 一致 → 取 quality 更丰富的（字符串更长的）
    - root 不一致 → 取 duration 更长的

    Args:
        piano_events: Piano 轨和弦事件。
        guitar_events: Guitar 轨和弦事件。

    Returns:
        合并后的统一和弦事件列表。
    """
    if not piano_events and not guitar_events:
        return []
    if not piano_events:
        return list(guitar_events)
    if not guitar_events:
        return list(piano_events)

    # 构建统一时间轴：所有 start/end 边界
    boundaries = sorted(
        set(
            [e.start for e in piano_events]
            + [e.end for e in piano_events]
            + [e.start for e in guitar_events]
            + [e.end for e in guitar_events]
        )
    )

    unified: list[ChordEvent] = []
    for i in range(len(boundaries) - 1):
        seg_start, seg_end = boundaries[i], boundaries[i + 1]
        p_match = _find_overlapping(piano_events, seg_start, seg_end)
        g_match = _find_overlapping(guitar_events, seg_start, seg_end)

        chosen = _pick_best(p_match, g_match, seg_start, seg_end)
        if chosen is not None:
            unified.append(chosen)

    return _merge_adjacent(unified)


def _find_overlapping(
    events: list[ChordEvent], start: float, end: float
) -> list[ChordEvent]:
    """找出与给定时间段有重叠的事件。"""
    return [e for e in events if e.start < end and e.end > start]


def _pick_best(
    piano_matches: list[ChordEvent],
    guitar_matches: list[ChordEvent],
    seg_start: float,
    seg_end: float,
) -> ChordEvent | None:
    """为一个时间段选取最佳和弦。"""
    if not piano_matches and not guitar_matches:
        return None
    if not piano_matches:
        return guitar_matches[0]
    if not guitar_matches:
        return piano_matches[0]

    p = piano_matches[0]
    g = guitar_matches[0]

    if p.root == g.root:
        richer = p if len(p.quality) >= len(g.quality) else g
        return ChordEvent(
            root=richer.root, quality=richer.quality, start=seg_start, end=seg_end
        )
    else:
        longer = p if p.duration >= g.duration else g
        return ChordEvent(
            root=longer.root, quality=longer.quality, start=seg_start, end=seg_end
        )


# ---------------------------------------------------------------------------
# Step 4: InversionMarking — 转位标记
# ---------------------------------------------------------------------------


def mark_inversions(
    unified_chords: list[ChordEvent],
    bass_progression: list[ChordEvent],
) -> list[ChordEvent]:
    """将 bass root 与 chord root 不同的和弦标记为 slash chord。

    例：C 和弦 + bass E → C/E（root="C", quality="/E"）。

    Args:
        unified_chords: 合并后的统一和弦列表。
        bass_progression: Bass progression 事件列表。

    Returns:
        标记了转位的和弦列表。
    """
    if not unified_chords or not bass_progression:
        return unified_chords

    result: list[ChordEvent] = []
    for chord in unified_chords:
        bass_roots = [
            b.root
            for b in bass_progression
            if b.start < chord.end and b.end > chord.start and b.root not in ("N", "X")
        ]
        if bass_roots:
            bass_root = max(set(bass_roots), key=bass_roots.count)
            if bass_root != chord.root:
                if chord.quality:
                    new_quality = f"{chord.quality}/{bass_root}"
                else:
                    new_quality = f"/{bass_root}"
                result.append(
                    ChordEvent(
                        root=chord.root,
                        quality=new_quality,
                        start=chord.start,
                        end=chord.end,
                    )
                )
            else:
                result.append(chord)
        else:
            result.append(chord)

    return result


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def refine(
    chord_events: dict[str, list[ChordEvent]],
    beat_timestamps: list[float],
    bass_progression: list[ChordEvent],
) -> list[ChordEvent]:
    """完整精炼流水线：节拍对齐 → 多轨合并 → 转位标记。

    Args:
        chord_events: 按 stem 分组的和弦事件，如 {"piano": [...], "guitar": [...]}。
        beat_timestamps: 拍点时间戳列表。
        bass_progression: Bass progression 事件列表。

    Returns:
        精炼后的统一和弦事件列表。
    """
    # Step 1: 逐 stem 节拍对齐
    refined_stems: dict[str, list[ChordEvent]] = {}
    for stem, events in chord_events.items():
        refined_stems[stem] = snap_to_beats(events, beat_timestamps)

    # Step 3: 多轨合并
    piano = refined_stems.get("piano", [])
    guitar = refined_stems.get("guitar", [])
    unified = merge_stem_chords(piano, guitar)

    # Step 4: 转位标记
    unified = mark_inversions(unified, bass_progression)

    return unified
