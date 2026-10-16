"""Paths shared by source runs and the standalone Windows backend."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_root() -> Path:
    if os.getenv("VMM_RESOURCE_DIR"):
        return Path(os.environ["VMM_RESOURCE_DIR"]).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def asr_worker_command(audio_path: str, result_path: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--asr-worker", audio_path, result_path]
    return [sys.executable, "-m", "backend.asr_worker", audio_path, result_path]
