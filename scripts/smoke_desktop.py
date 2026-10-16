"""Launch the packaged Electron window and verify its preload and local UI."""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("executable", type=Path)
args = parser.parse_args()
executable = args.executable.resolve()
environment = dict(os.environ, PATH="")
environment.pop("ELECTRON_RUN_AS_NODE", None)
log = Path(tempfile.gettempdir()) / "VideoMagicMix-original-startup.log"
offset = log.stat().st_size if log.exists() else 0
with tempfile.TemporaryDirectory(prefix="VMM desktop ") as profile:
    result = subprocess.run([str(executable), "--smoke-test", "--user-data-dir=" + profile],
                            cwd=profile, env=environment, timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    fresh = log.read_bytes()[offset:].decode("utf-8", errors="replace") if log.exists() else ""
    if result.returncode or "DESKTOP_SMOKE_OK" not in fresh:
        raise RuntimeError(f"Desktop failed (exit={result.returncode}):\n{fresh}")
    print("PASS packaged Electron window, backend startup, page and preload bridge; PATH is empty")
