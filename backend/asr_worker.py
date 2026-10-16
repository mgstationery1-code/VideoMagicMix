from __future__ import annotations

import json
import sys
from pathlib import Path

from .asr import FasterWhisperEngine, transcript_json


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python -m backend.asr_worker AUDIO OUTPUT", file=sys.stderr)
        return 2
    audio_path, output_path = sys.argv[1], Path(sys.argv[2])
    try:
        transcript = FasterWhisperEngine().transcribe(audio_path)
        payload = {"ok": True, "transcript": transcript_json(transcript)}
    except BaseException as exc:  # Worker must always return a machine-readable failure.
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
