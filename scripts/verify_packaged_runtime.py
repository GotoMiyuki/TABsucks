"""Write a small JSON report for a packaged TABsucks runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    output = Path(sys.argv[1]).resolve()
    output.write_text('{"stage": "starting"}', encoding="utf-8")

    import torch

    output.write_text('{"stage": "torch-imported"}', encoding="utf-8")

    import onnxruntime

    report = {
        "stage": "complete",
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "torch_cuda_available": torch.cuda.is_available(),
        "onnxruntime_version": onnxruntime.__version__,
        "onnxruntime_providers": onnxruntime.get_available_providers(),
    }
    output.write_text(
        json.dumps(report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
