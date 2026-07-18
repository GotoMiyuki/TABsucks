"""Prepare FFmpeg executables for the Windows distribution."""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    destination = project_root / "packaging" / "ffmpeg"
    destination.mkdir(parents=True, exist_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise

        ffmpeg, ffprobe = get_or_fetch_platform_executables_else_raise(
            download_dir=str(destination)
        )

    for source, filename in ((ffmpeg, "ffmpeg.exe"), (ffprobe, "ffprobe.exe")):
        source_path = Path(source).resolve()
        target_path = destination / filename
        if source_path != target_path.resolve():
            shutil.copy2(source_path, target_path)
        print(f"Prepared {target_path}")


if __name__ == "__main__":
    main()
