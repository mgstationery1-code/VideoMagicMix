"""Detect changed inputs before consuming an approved preflight plan."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .mixer import list_videos
from .models import BodyConfig, Project


def project_fingerprint(project: Project) -> str:
    config = asdict(project)
    # Candidate counts and scan statistics are derived values, not edits. A
    # refresh or first-time default allocation must not invalidate its own plan.
    config["body_configs"] = {}
    for scheme in project.schemes:
        configs = {}
        for segment in scheme.segments:
            if not segment.enabled or segment.row_key.lower() == "hook":
                continue
            body = project.body_configs.get(scheme.scheme_id, {}).get(segment.row_key)
            body = body or BodyConfig(scheme.scheme_id, segment.row_key)
            configs[segment.row_key] = {key: getattr(body, key) for key in (
                "threshold", "min_speed", "user_configured", "excluded_combinations")}
        config["body_configs"][scheme.scheme_id] = configs
    paths = set(list_videos(project.hook_dir)) if project.hook_dir else set()
    for folder in set(project.body_mapping.values()):
        paths.update(list_videos(folder))
    paths.update(scheme.voice_path for scheme in project.schemes if scheme.voice_path)
    paths.update(path for path in (project.bgm_path, project.excel_path) if path)
    media = []
    for path in sorted(paths):
        try:
            stat = Path(path).stat()
            media.append((path, stat.st_size, stat.st_mtime_ns))
        except OSError:
            media.append((path, None, None))
    directories = [(path, Path(path).is_dir()) for path in sorted({
        project.hook_dir, project.body_root_dir, project.output_dir, *project.body_mapping.values()
    }) if path]
    payload = json.dumps([config, media, directories], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
