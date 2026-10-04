"""Measured progress adapter for audio-separator 0.44's BS-RoFormer engine.

Only this engine instance is instrumented; library modules and global tqdm
functions are never changed. Unrecognized inference layouts remain indeterminate.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import time
from pathlib import Path

import requests
from audio_separator.separator import Separator

logger = logging.getLogger(__name__)


def report(callback, stage: str, completed=None, total=None, *, unit=None, detail=None):
    """A fraction describes this measured stage, not estimated total duration."""
    if callback is None:
        return
    progress = min(completed / total, 1.0) if total and completed is not None else None
    try:
        callback(progress, stage=stage, completed=completed, total=total, unit=unit, detail=detail)
    except Exception:
        logger.exception("Could not publish separation progress")


def roformer_chunk_count(model, sample_count: int) -> int | None:
    """Mirror the 0.44 RoFormer loop's stride using the actual demix input."""
    if not getattr(model, "is_roformer", False) or getattr(model, "pitch_shift", 0) != 0:
        return None
    try:
        config = model.model_data_cfgdict
        dim_t = model.segment_size if model.override_model_segment_size else config.inference.dim_t
        hop = getattr(config.model, "stft_hop_length", None) or config.audio.hop_length
        chunk_size = int(hop) * (int(dim_t) - 1)
        desired_step = int(model.overlap * config.audio.sample_rate)
        step = chunk_size if desired_step <= 0 else min(desired_step, chunk_size)
        return math.ceil(sample_count / step) if step > 0 and sample_count > 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


class ProgressAudioSeparator(Separator):
    def __init__(self, *args, progress_callback=None, **kwargs):
        self.progress_callback = progress_callback
        super().__init__(*args, **kwargs)

    def download_file_if_not_exists(self, url, output_path):
        path = Path(output_path)
        if path.is_file():
            return
        report(self.progress_callback, "downloading_model", detail=path.name, unit="bytes")
        # Publish only complete downloads. An interrupted .part file must not be
        # mistaken for a valid cached checkpoint on the next attempt.
        temporary = None
        try:
            with requests.get(url, stream=True, timeout=(30, 300)) as response:
                if response.status_code != 200:
                    # Keep the vendor's RuntimeError contract for repository fallback.
                    raise RuntimeError(f"Failed to download file from {url}, response code: {response.status_code}")
                encoded = response.headers.get("content-encoding", "identity") != "identity"
                total = None if encoded else int(response.headers.get("content-length", 0)) or None
                completed = 0
                last_report = time.monotonic()
                report(self.progress_callback, "downloading_model", 0, total, unit="bytes", detail=path.name)
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".part", delete=False) as output:
                    temporary = Path(output.name)
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        output.write(chunk)
                        completed += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 0.25:
                            report(self.progress_callback, "downloading_model", completed, total, unit="bytes", detail=path.name)
                            last_report = now
                if total is not None and completed != total:
                    raise RuntimeError(f"Incomplete model download: {path.name}: {completed}/{total} bytes")
                os.replace(temporary, path)
                temporary = None
                report(self.progress_callback, "downloading_model", completed, total, unit="bytes", detail=path.name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def download_model_files(self, model_filename):
        result = super().download_model_files(model_filename)
        report(self.progress_callback, "loading_model", detail=model_filename)
        return result

    def _demix_with_progress(self, model, original, *args, **kwargs):
        mix = kwargs.get("mix", args[0] if args else None)
        total = roformer_chunk_count(model, mix.shape[-1]) if mix is not None else None
        run = getattr(model, "model_run", None)
        completed = 0
        hook = None
        report(self.progress_callback, "separating_audio", 0 if total else None, total, unit="chunks")

        def chunk_finished(_module, _inputs, _output):
            nonlocal completed
            completed += 1
            # GPU execution is asynchronous. Synchronize before declaring a
            # forward pass complete, otherwise its queued kernels look finished.
            import torch

            device = next(run.parameters()).device
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            report(self.progress_callback, "separating_audio", completed, total, unit="chunks")

        try:
            if total is not None and run is not None and hasattr(run, "register_forward_hook"):
                hook = run.register_forward_hook(chunk_finished)
            return original(*args, **kwargs)
        finally:
            if hook is not None:
                hook.remove()

    def separate(self, *args, **kwargs):
        model = getattr(self, "model_instance", None)
        if self.progress_callback is None or model is None:
            return super().separate(*args, **kwargs)
        originals = {}
        completed_stems = 0
        instruments = getattr(getattr(model, "model_data_cfgdict", None), "training", None)
        total_stems = len(getattr(instruments, "instruments", [])) or None

        def replace(name, wrapper):
            if not hasattr(model, name):
                return
            originals[name] = (name in vars(model), vars(model).get(name))
            setattr(model, name, wrapper)

        original_demix = getattr(model, "demix", None)
        original_write = getattr(model, "final_process", None)

        def demix(*a, **kw):
            result = self._demix_with_progress(model, original_demix, *a, **kw)
            report(self.progress_callback, "writing_stems", 0, total_stems, unit="stems")
            return result

        def write(stem_path, source, stem_name):
            nonlocal completed_stems
            result = original_write(stem_path, source, stem_name)
            completed_stems += 1
            report(self.progress_callback, "writing_stems", completed_stems, total_stems, unit="stems", detail=stem_name)
            return result

        try:
            if original_demix is not None:
                replace("demix", demix)
            if original_write is not None:
                replace("final_process", write)
            return super().separate(*args, **kwargs)
        finally:
            for name, (existed, value) in originals.items():
                if existed:
                    setattr(model, name, value)
                else:
                    delattr(model, name)
