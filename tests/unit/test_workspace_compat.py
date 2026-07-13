"""Compatibility tests for the deprecated kernel workspace module."""

from __future__ import annotations

import numpy as np

from src.kernel.core.workspace import Workspace
from src.plugins.separation import SeparationResult


def test_workspace_analysis_target_uses_current_separation_exports() -> None:
    ws = Workspace()
    ws.set_analysis_track("bass")

    expected = np.array([[1.0, 2.0], [3.0, 4.0]])
    ws._separation_result = SeparationResult(
        vocals=np.zeros((2, 2)),
        drums=np.zeros((2, 2)),
        bass=expected,
        piano=np.zeros((2, 2)),
        guitar=np.zeros((2, 2)),
        other=np.zeros((2, 2)),
        sample_rate=44100,
    )

    np.testing.assert_array_equal(ws.get_analysis_target_data(), expected)
