"""Standalone backend. Models and media tools live beside it, not in .venv.

Using onedir avoids unpacking the inference DLLs on every ASR worker launch.
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = [], [], ["backend.main", "backend.asr_worker"]
for package in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av", "rapidfuzz"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in ("faster-whisper", "ctranslate2", "huggingface-hub", "tokenizers", "av"):
    datas += copy_metadata(distribution)

a = Analysis([str(root / "run_backend.py")], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "pytest", "IPython"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="videomagicmix-backend",
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="videomagicmix-backend")
