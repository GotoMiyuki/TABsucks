from __future__ import annotations

import io

import pretty_midi
import pytest

from src.kernel.core.midi_exporter import (
    MidiExporterError,
    export_chord_tracks_to_midi,
)


def test_export_chord_tracks_creates_readable_multitrack_midi() -> None:
    data = export_chord_tracks_to_midi(
        {
            "bass": [
                {"start": 0.0, "end": 2.0, "name": "C:maj"},
                {"start": 2.0, "end": 4.0, "name": "A:min"},
            ],
            "guitar": [
                {"start": 0.0, "end": 4.0, "name": "G:7"},
            ],
        }
    )

    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))

    assert [instrument.name for instrument in midi.instruments] == [
        "BASS",
        "GUITAR",
    ]
    assert len(midi.instruments[0].notes) == 6
    assert len(midi.instruments[1].notes) == 4
    assert midi.instruments[0].notes[0].start == pytest.approx(0.0)
    assert midi.instruments[0].notes[-1].end == pytest.approx(4.0)


def test_export_chord_tracks_rejects_empty_or_no_chord_data() -> None:
    with pytest.raises(MidiExporterError, match="没有可导出的有效和弦"):
        export_chord_tracks_to_midi(
            {
                "bass": [
                    {"start": 0.0, "end": 2.0, "name": "N"},
                ]
            }
        )
