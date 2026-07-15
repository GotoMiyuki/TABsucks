"""Bridge to ChordMini checkpoint utilities.

The chordmini submodule uses relative imports that don't work when loaded
dynamically.  The three extraction functions are inlined here to avoid the
import machinery complexity.
"""

import logging

_log = logging.getLogger("chordmini")


def info(msg: str) -> None:
    _log.info(msg)


def warning(msg: str) -> None:
    _log.warning(msg)


def error(msg: str) -> None:
    _log.error(msg)


def extract_model_state_dict(checkpoint: dict) -> dict:
    """Return the model state dict from a checkpoint (handles DDP prefix)."""
    state_dict = checkpoint.get("model_state_dict",
                                checkpoint.get("model", checkpoint))
    if (isinstance(state_dict, dict) and state_dict
            and next(iter(state_dict)).startswith("module.")):
        state_dict = {k.replace("module.", ""): v for k, v in state_dict.items()}
    return state_dict


def extract_normalization_stats(checkpoint: dict,
                                default_mean: float = 0.0,
                                default_std: float = 1.0) -> tuple[float, float]:
    """Return (mean, std) from checkpoint, falling back to defaults."""
    mean = checkpoint.get("mean", default_mean)
    std = checkpoint.get("std", default_std)
    if "normalization" in checkpoint and isinstance(checkpoint["normalization"], dict):
        norm = checkpoint["normalization"]
        mean = norm.get("mean", mean)
        std = norm.get("std", std)
    if hasattr(mean, "item"):
        mean = float(mean.item())
    if hasattr(std, "item"):
        std = float(std.item())
    return float(mean), max(float(std), 1e-8)


def extract_state_dict_and_stats(checkpoint: dict,
                                 default_mean: float = 0.0,
                                 default_std: float = 1.0) -> tuple[dict, float, float]:
    """Return (state_dict, mean, std) from a ChordMini checkpoint."""
    return (
        extract_model_state_dict(checkpoint),
        *extract_normalization_stats(checkpoint,
                                     default_mean=default_mean,
                                     default_std=default_std),
    )
