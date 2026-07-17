# ruff: noqa: F821, I001, UP009
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).resolve().parent
datas = []


def add_tree(relative_path, destination=None):
    source = project_root / relative_path
    if source.exists():
        if destination is None:
            destination = (
                str(Path(relative_path).parent)
                if source.is_file()
                else relative_path
            )
        datas.append((str(source), destination))


add_tree("src/ui/static")
add_tree("src/ui/mock_data")
add_tree("src/plugins/separation/model_1/manifest.json")
add_tree("src/plugins/chord/manifest.json")
add_tree("src/plugins/rhythm/manifest.json")
add_tree("src/plugins/chord/external/ismir2019")
add_tree("src/plugins/chord/external/chordmini/config")
add_tree("src/plugins/chord/external/chordmini/checkpoints")
add_tree("src/plugins/chord/external/chordmini/src")
add_tree("pretrained")
add_tree("models")
add_tree("packaging/ffmpeg", "ffmpeg")

hiddenimports = [
    "src.plugins._example_separator",
    "src.plugins._example_analyzer",
    "src.plugins.separation.model_1.separator",
    "src.plugins.chord.chordnet_2e1d",
    "src.plugins.chord.btc_sl",
    "src.plugins.chord.ismir2019",
    "src.plugins.chord.bass_root",
    "src.plugins.rhythm.foundation",
]

for package in (
    "audio_separator",
    "fastapi",
    "librosa",
    "static_ffmpeg",
    "uvicorn",
):
    try:
        hiddenimports.extend(collect_submodules(package))
        datas.extend(collect_data_files(package))
    except Exception:
        pass

a = Analysis(
    [str(project_root / "scripts" / "tabsucks_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TABsucks",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TABsucks",
    contents_directory=".",
)
