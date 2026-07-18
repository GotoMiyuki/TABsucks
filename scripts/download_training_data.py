"""FMA (Free Music Archive) 训练数据下载脚本。

下载 FMA small 子集用于 Stage 1 伪标签训练。
UnlabeledAudioDataset 会递归扫描目录下的音频文件。

Usage:
    py scripts/download_training_data.py --subset small --output_dir data/fma_audio
    py scripts/download_training_data.py --subset small --max_tracks 500
"""

from __future__ import annotations

import argparse
import os
import shutil
import zipfile
from pathlib import Path

# FMA 官方下载地址
FMA_BASE_URL = "https://os.unil.cloud.switch.ch/fma"
FMA_FILES = {
    "small": "fma_small.zip",       # ~7.2 GB, 8000 tracks, 30s each
    "medium": "fma_medium.zip",     # ~22 GB, 25000 tracks
    "metadata": "fma_metadata.zip", # ~300 MB, CSV metadata
}

DEFAULT_OUTPUT = "data/fma_audio"


def parse_args():
    p = argparse.ArgumentParser(description="Download FMA dataset for chord model training")
    p.add_argument("--subset", default="small", choices=["small", "medium"],
                   help="FMA 子集 (small: 8000 tracks ~7.2GB, medium: 25000 tracks ~22GB)")
    p.add_argument("--output_dir", default=DEFAULT_OUTPUT, help="输出目录")
    p.add_argument("--max_tracks", type=int, default=None, help="限制解压的音频数量（调试用）")
    p.add_argument("--keep_zip", action="store_true", help="下载后保留 zip 文件")
    return p.parse_args()


def download_file(url: str, dest: Path):
    """下载文件，显示进度。"""
    import urllib.request

    if dest.exists():
        print(f"[download] Already exists: {dest}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[download] Downloading {url}")
    print(f"[download] → {dest}")

    def _progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            mb = downloaded / (1024 * 1024)
            total_mb = total_size / (1024 * 1024)
            print(f"\r[download] {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)

    urllib.request.urlretrieve(url, str(dest), reporthook=_progress)
    print()


def extract_audio_flatten(zip_path: Path, output_dir: Path, max_tracks: int | None = None):
    """从 FMA zip 中提取音频文件到扁平目录结构。

    FMA zip 内部结构: fma_small/000/000002.mp3, fma_small/001/001005.mp3, ...
    提取后: data/fma_audio/000002.mp3, data/fma_audio/001005.mp3, ...
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_exts = (".mp3", ".wav", ".flac", ".ogg", ".m4a")
    count = 0

    print(f"[extract] Extracting audio from {zip_path}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        audio_entries = [e for e in zf.namelist()
                         if any(e.lower().endswith(ext) for ext in audio_exts)
                         and not e.endswith("/")]
        total = len(audio_entries)
        print(f"[extract] Found {total} audio files in archive")

        if max_tracks and max_tracks < total:
            audio_entries = audio_entries[:max_tracks]
            print(f"[extract] Limiting to {max_tracks} tracks")

        for entry in audio_entries:
            if max_tracks and count >= max_tracks:
                break
            # 只取文件名，丢弃目录结构
            filename = Path(entry).name
            if not filename:
                continue
            dest_path = output_dir / filename
            if dest_path.exists():
                count += 1
                continue
            with zf.open(entry) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
            if count % 100 == 0:
                print(f"\r[extract] {count}/{len(audio_entries)} extracted", end="", flush=True)

    print(f"\n[extract] Done: {count} audio files → {output_dir}")


def main():
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    output_dir = project_root / args.output_dir
    zip_name = FMA_FILES[args.subset]
    zip_url = f"{FMA_BASE_URL}/{zip_name}"
    zip_path = project_root / "data" / zip_name

    print(f"{'='*60}")
    print(f"FMA {args.subset} download")
    print(f"Output: {output_dir}")
    if args.max_tracks:
        print(f"Max tracks: {args.max_tracks}")
    print(f"{'='*60}")

    # 1. 下载
    download_file(zip_url, zip_path)

    # 2. 解压音频（扁平化）
    extract_audio_flatten(zip_path, output_dir, args.max_tracks)

    # 3. 清理
    if not args.keep_zip and zip_path.exists():
        print(f"[cleanup] Removing {zip_path}")
        zip_path.unlink()

    # 4. 统计
    audio_files = list(output_dir.glob("*.*"))
    audio_files = [f for f in audio_files if f.suffix.lower() in (".mp3", ".wav", ".flac")]
    total_mb = sum(f.stat().st_size for f in audio_files) / (1024 * 1024)
    print(f"\n{'='*60}")
    print(f"Download complete!")
    print(f"Files: {len(audio_files)}")
    print(f"Total size: {total_mb:.1f} MB")
    print(f"Directory: {output_dir}")
    print(f"\nTo train with this data:")
    print(f"  py scripts/train_chord_model.py --audio_dir {args.output_dir} --model_type ChordNet")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
