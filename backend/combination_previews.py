"""Explicit, cached Body video previews. Listing candidates never renders media."""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .mixer import Selection, combination_id, resolve_binary
from .runtime import text_subprocess_kwargs


def preview_key(selection: Selection) -> str:
    sources = [(str(Path(item.path).resolve()), Path(item.path).stat().st_size,
                Path(item.path).stat().st_mtime_ns) for item in selection.files]
    payload = json.dumps([sources, selection.target_duration, "540x960-v1"], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_body_preview(selection: Selection, output: Path) -> None:
    """Render muted full combination with exactly the final timeline's speed."""
    command = [resolve_binary("ffmpeg.exe"), "-y", "-hide_banner", "-loglevel", "error"]
    filters = []
    for index, item in enumerate(selection.files):
        command += ["-i", item.path]
        filters.append(f"[{index}:v]trim=duration={item.duration:.6f},setpts=PTS-STARTPTS,"
                       f"scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
                       f"setsar=1,format=yuv420p[v{index}]")
    labels = "".join(f"[v{index}]" for index in range(len(selection.files)))
    filters.append(labels + f"concat=n={len(selection.files)}:v=1:a=0[joined]")
    factor = selection.target_duration / selection.source_duration
    filters.append(f"[joined]setpts=PTS*{factor:.9f},fps=30[out]")
    temporary = output.with_suffix(".partial.mp4")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]", "-an", "-t",
                f"{selection.target_duration:.6f}", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, **text_subprocess_kwargs())
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:] or "预渲染失败")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


class PreviewStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="body-preview")
        self.tasks: dict[str, dict] = {}
        self.assets: dict[str, Path] = {}
        self.lock = threading.Lock()
        self.active: dict[str, str] = {}

    def ready(self, selection: Selection) -> str | None:
        try:
            key = preview_key(selection)
        except OSError:
            return None
        path = self.directory / f"body-{key}.mp4"
        if path.is_file():
            self.assets[key] = path
            return key
        return None

    def start(self, owner: str, selections: list[Selection], enabled: Callable[[Selection], bool]) -> dict:
        with self.lock:
            current = self.active.get(owner)
            if current and self.tasks[current]["status"] in {"queued", "running"}:
                return self.tasks[current]
            task_id = uuid.uuid4().hex
            task = {"task_id": task_id, "status": "queued", "total": len(selections),
                    "completed": 0, "skipped": 0, "errors": [], "previews": {}}
            self.tasks[task_id] = task
            self.active[owner] = task_id
            self.executor.submit(self._run, task, selections, enabled)
            return task

    def _run(self, task: dict, selections: list[Selection], enabled: Callable[[Selection], bool]) -> None:
        task["status"] = "running"
        for selection in selections:
            if not enabled(selection):
                task["skipped"] += 1
                continue
            try:
                key = self.ready(selection)
                if key is None:
                    key = preview_key(selection)
                    path = self.directory / f"body-{key}.mp4"
                    render_body_preview(selection, path)
                    self.assets[key] = path
                task["previews"][combination_id(selection)] = key
                task["completed"] += 1
            except Exception as exc:
                names = " + ".join(Path(item.path).name for item in selection.files)
                task["errors"].append(f"{names}：{exc}")
        task["status"] = "completed" if not task["errors"] else "failed"
