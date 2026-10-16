from __future__ import annotations

import itertools
import json
import math
import os
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from .runtime import resource_root


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".ts"}


def resolve_binary(name: str, configured: str | None = None) -> str:
    """Resolve only this installation's tools before falling back to PATH."""
    candidates = []
    if configured:
        candidates.append(Path(configured))
    root = resource_root()
    candidates.append(root / "tools" / name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return name


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_audio: bool = True


@dataclass
class Selection:
    files: list[MediaInfo]
    source_duration: float
    target_duration: float
    speed_factor: float
    threshold: float
    accepted: bool
    message: str = ""


def list_videos(folder: str) -> list[str]:
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def probe_duration(path: str, ffprobe: str | None = None) -> float:
    ffprobe = resolve_binary("ffprobe.exe", ffprobe)
    command = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"无法读取视频时长：{path}；请确认 FFprobe 可用。") from exc


def probe_media(path: str, ffprobe: str | None = None) -> MediaInfo:
    ffprobe = resolve_binary("ffprobe.exe", ffprobe)
    command = [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        payload = json.loads(result.stdout)
        duration = float(payload.get("format", {}).get("duration", 0))
        if duration <= 0:
            raise ValueError("duration")
        has_audio = any(stream.get("codec_type") == "audio" for stream in payload.get("streams", []))
        return MediaInfo(path=path, duration=duration, has_audio=has_audio)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取媒体文件：{path}；文件可能损坏或 FFprobe 不可用。") from exc


def complete_hook_candidates(files: Iterable[MediaInfo]) -> list[Selection]:
    """Every valid Hook is a complete, unmodified candidate."""
    return [Selection([item], item.duration, item.duration, 1.0, 1.0, True) for item in sorted(files, key=lambda value: value.path)]


def body_combination(files: Iterable[MediaInfo], target: float, threshold: float, max_items: int = 8) -> Selection:
    items = list(files)
    if threshold < 1.0:
        raise ValueError("Body 变速阈值必须大于或等于 1.0")
    low, high = target / threshold, target * threshold
    best: tuple[float, list[MediaInfo], float] | None = None
    if len(items) > 18:
        random.shuffle(items)
        items = items[:18]
    for count in range(1, min(max_items, len(items)) + 1):
        for combo in itertools.combinations(items, count):
            duration = sum(item.duration for item in combo)
            distance = abs(duration - target)
            if best is None or distance < best[0]:
                best = (distance, list(combo), duration)
            if low <= duration <= high:
                return Selection(list(combo), duration, target, target / duration, threshold, True)
    if best is None:
        return Selection([], 0.0, target, 1.0, threshold, False, "没有可用 Body 视频")
    _, combo, duration = best
    needed = max(target / duration, duration / target) if duration else None
    message = f"无符合阈值的组合；最近组合 {duration:.2f} 秒，建议 Body 阈值至少 {needed:.3f}。"
    return Selection(combo, duration, target, target / duration if duration else 1.0, threshold, False, message)


def body_combinations(files: Iterable[MediaInfo], target: float, threshold: float, max_items: int = 8, max_results: int = 500) -> list[Selection]:
    """Return every accepted, non-repeating combination ordered by closeness."""
    items = _bounded_body_items(files, target)
    if threshold < 1.0:
        raise ValueError("Body 变速阈值必须大于或等于 1.0")
    if target <= 0 or not items:
        return []
    low, high = target / threshold, target * threshold
    results: list[Selection] = []
    for count in range(1, min(max_items, len(items)) + 1):
        for combo in itertools.combinations(items, count):
            duration = sum(item.duration for item in combo)
            if low <= duration <= high:
                results.append(Selection(list(combo), duration, target, target / duration, threshold, True))
    results.sort(key=lambda selection: (abs(selection.source_duration - target), len(selection.files), tuple(item.path for item in selection.files)))
    return results[:max_results]


def _bounded_body_items(files: Iterable[MediaInfo], target: float, limit: int = 18) -> list[MediaInfo]:
    """Keep combination enumeration bounded while retaining deterministic inputs."""
    items = sorted(list(files), key=lambda item: (abs(item.duration - target), item.path))
    return items[:limit]


def body_threshold_for_count(files: Iterable[MediaInfo], target: float, candidate_count: int, max_items: int = 8) -> float | None:
    """Return the smallest 3-decimal threshold that admits at least N combinations."""
    if target <= 0 or candidate_count <= 0:
        return None
    items = _bounded_body_items(files, target)
    requirements: list[tuple[float, float, int, tuple[str, ...]]] = []
    for count in range(1, min(max_items, len(items)) + 1):
        for combo in itertools.combinations(items, count):
            duration = sum(item.duration for item in combo)
            if duration <= 0:
                continue
            required = max(target / duration, duration / target, 1.0)
            requirements.append((required, abs(duration - target), count, tuple(item.path for item in combo)))
    if not requirements:
        return None
    requirements.sort()
    required = requirements[min(candidate_count, len(requirements)) - 1][0]
    return math.ceil(required * 1000 - 1e-9) / 1000


def ffmpeg_video_filter(index: int, source_duration: float, target_duration: float, width: int, height: int) -> str:
    speed = target_duration / source_duration
    return f"[{index}:v]setpts=(PTS-STARTPTS)*{speed:.8f},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[v{index}]"


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render_video(selections: list[Selection], voice_path: str, output_path: str, width: int = 1080, height: int = 1920, ffmpeg: str | None = None, subtitle_path: str | None = None, subtitle_fonts_dir: str | None = None) -> str:
    """Render one complete Hook followed by muted Body and append Body narration.

    selections[0] is always a single finished Hook.  Its video timing and audio
    are retained.  Remaining selections are Body sections whose source audio is
    intentionally never referenced.
    """
    if not selections or not voice_path:
        raise ValueError("渲染至少需要一个视频段和一条配音")
    ffmpeg = resolve_binary("ffmpeg.exe", ffmpeg)
    inputs: list[str] = []
    filters: list[str] = []
    section_labels: list[str] = []
    for section_index, selection in enumerate(selections):
        source_labels: list[str] = []
        for file_info in selection.files:
            input_index = len(inputs)
            inputs.append(file_info.path)
            label = f"s{section_index}_{input_index}"
            trim = "" if section_index == 0 else f"trim=duration={file_info.duration:.6f},"
            filters.append(f"[{input_index}:v]{trim}setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p[{label}]")
            source_labels.append(f"[{label}]")
        if len(source_labels) == 1:
            joined = source_labels[0]
        else:
            joined = f"[joined{section_index}]"
            filters.append("".join(source_labels) + f"concat=n={len(source_labels)}:v=1:a=0{joined}")
        factor = selection.target_duration / max(selection.source_duration, 0.001)
        section_label = f"section{section_index}"
        filters.append(f"{joined}setpts=PTS-STARTPTS, setpts=PTS*{factor:.8f}[{section_label}]")
        section_labels.append(f"[{section_label}]")
    final_video = "[video]"
    filters.append("".join(section_labels) + f"concat=n={len(section_labels)}:v=1:a=0{final_video}")
    if subtitle_path:
        safe_subtitle = _escape_filter_path(subtitle_path)
        filter_name = "ass" if Path(subtitle_path).suffix.lower() == ".ass" else "subtitles"
        font_option = f":fontsdir='{_escape_filter_path(subtitle_fonts_dir)}'" if subtitle_fonts_dir else ""
        filters.append(f"{final_video}{filter_name}='{safe_subtitle}'{font_option}[subtitled]")
        final_video = "[subtitled]"
    voice_index = len(inputs)
    inputs.append(voice_path)
    hook_duration = selections[0].source_duration
    body_duration = sum(selection.target_duration for selection in selections[1:])
    filters.append(f"[0:a:0]apad=whole_dur={hook_duration:.6f},atrim=duration={hook_duration:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[hookaudio]")
    filters.append(f"[{voice_index}:a:0]atrim=duration={body_duration:.6f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[bodyaudio]")
    filters.append("[hookaudio][bodyaudio]concat=n=2:v=0:a=1[audio]")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y"]
    for item in inputs:
        command.extend(["-i", item])
    command.extend(["-filter_complex", "; ".join(filters), "-map", final_video, "-map", "[audio]", "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    except OSError as exc:
        raise RuntimeError("找不到 FFmpeg，请安装 FFmpeg 并将其加入 PATH。") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:] or "FFmpeg 渲染失败")
    return output_path
