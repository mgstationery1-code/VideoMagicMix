from __future__ import annotations

import itertools
import json
import math
import os
import subprocess
import threading
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from .runtime import resource_root, text_subprocess_kwargs


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".ts"}
_MEDIA_CACHE: dict[tuple[str, int, int], MediaInfo] = {}
_MEDIA_CACHE_LOCK = threading.Lock()


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
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60, **text_subprocess_kwargs())
        return float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"无法读取视频时长：{path}；请确认 FFprobe 可用。") from exc


def probe_media(path: str, ffprobe: str | None = None) -> MediaInfo:
    ffprobe = resolve_binary("ffprobe.exe", ffprobe)
    command = [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60, **text_subprocess_kwargs())
        payload = json.loads(result.stdout)
        duration = float(payload.get("format", {}).get("duration", 0))
        if duration <= 0:
            raise ValueError("duration")
        has_audio = any(stream.get("codec_type") == "audio" for stream in payload.get("streams", []))
        return MediaInfo(path=path, duration=duration, has_audio=has_audio)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取媒体文件：{path}；文件可能损坏或 FFprobe 不可用。") from exc


def probe_media_cached(path: str, ffprobe: str | None = None) -> MediaInfo:
    """Reuse FFprobe results while a file's size and mtime remain unchanged."""
    try:
        stat = Path(path).stat()
        key = (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return probe_media(path, ffprobe)
    with _MEDIA_CACHE_LOCK:
        cached = _MEDIA_CACHE.get(key)
        if cached is not None:
            return cached
    result = probe_media(path, ffprobe)
    with _MEDIA_CACHE_LOCK:
        # Keep the cache bounded and discard stale entries for this path.
        for old_key in [item for item in _MEDIA_CACHE if item[0] == key[0] and item != key]:
            _MEDIA_CACHE.pop(old_key, None)
        _MEDIA_CACHE[key] = result
    return result


def complete_hook_candidates(files: Iterable[MediaInfo]) -> list[Selection]:
    """Every valid Hook is a complete, unmodified candidate."""
    return [Selection([item], item.duration, item.duration, 1.0, 1.0, True) for item in sorted(files, key=lambda value: value.path)]


def body_combination(files: Iterable[MediaInfo], target: float, threshold: float, max_items: int = 8, min_speed: float = 0.9) -> Selection:
    files = list(files)
    accepted = body_combinations(files, target, threshold, max_items=max_items, min_speed=min_speed)
    if accepted:
        return accepted[0]
    all_options = body_combinations(files, target, float("inf"), max_items=max_items, min_speed=0.000001)
    nearest = all_options[0] if all_options else None
    if nearest:
        speed = nearest.source_duration / target
        nearest.accepted = False
        nearest.message = f"当前倍速范围 {min_speed:.3f}–{threshold:.3f}× 没有启用组合；最近组合需要 {speed:.3f}×，请调整范围/候选数量，或检查是否取消勾选了所有组合。"
        return nearest
    return Selection([], 0, target, 1, threshold, False, "没有可用 Body 视频")


def combination_id(selection: Selection) -> str:
    # File order is part of the identity; directory scan order cannot change it.
    payload = "\n".join(str(Path(item.path).resolve()).casefold() for item in selection.files)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def body_speed_range_for_count(files: Iterable[MediaInfo], target: float, candidate_count: int, excluded: Iterable[str] = ()) -> tuple[float, float] | None:
    options = body_combinations(files, target, float("inf"), max_results=70000, min_speed=0.000001)
    excluded = set(excluded)
    if excluded:
        options = [item for item in options if combination_id(item) not in excluded]
    if not options:
        return None
    speeds = [item.source_duration / target for item in options]
    # Keep every default candidate, then admit the closest speeds outside the
    # default window. Ties are admitted together rather than silently dropped.
    ranked = sorted(speeds, key=lambda speed: (max(0.9 - speed, speed - 1.4, 0), abs(speed - 1)))
    distance = max(0.9 - ranked[min(candidate_count, len(ranked)) - 1], ranked[min(candidate_count, len(ranked)) - 1] - 1.4, 0)
    low = max(0.000001, math.floor((0.9 - distance) * 1000 + 1e-9) / 1000)
    high = math.ceil((1.4 + distance) * 1000 - 1e-9) / 1000
    if candidate_count < sum(0.9 - 1e-9 <= speed <= 1.4 + 1e-9 for speed in speeds):
        # For smaller requested pools, shrink around normal speed.
        ranked = sorted(speeds, key=lambda speed: (abs(speed - 1), speed))
        radius = abs(ranked[min(candidate_count, len(ranked)) - 1] - 1)
        low = max(0.000001, math.floor((1 - radius) * 1000 + 1e-9) / 1000)
        high = math.ceil((1 + radius) * 1000 - 1e-9) / 1000
    return low, high


def body_combinations(files: Iterable[MediaInfo], target: float, threshold: float, max_items: int = 8, max_results: int = 70000, min_speed: float = 0.9) -> list[Selection]:
    """Return every accepted, non-repeating combination ordered by closeness."""
    items = _bounded_body_items(files, target)
    if not math.isfinite(min_speed) or min_speed <= 0 or threshold < min_speed or math.isnan(threshold):
        raise ValueError("Body 倍速范围必须是有效正数，且最低倍速不能高于最高倍速")
    if target <= 0 or not items:
        return []
    high = target * threshold
    results: list[Selection] = []
    for count in range(1, min(max_items, len(items)) + 1):
        for combo in itertools.combinations(items, count):
            duration = sum(item.duration for item in combo)
            if target * min_speed - 1e-8 <= duration <= high + 1e-8:
                results.append(Selection(list(combo), duration, target, target / duration, threshold, True))
    results.sort(key=lambda selection: (abs(selection.source_duration - target), len(selection.files), tuple(item.path for item in selection.files)))
    return results[:max_results]


def _bounded_body_items(files: Iterable[MediaInfo], target: float, limit: int = 18) -> list[MediaInfo]:
    """Keep combination enumeration bounded while retaining deterministic inputs."""
    items = sorted(list(files), key=lambda item: (abs(item.duration - target), item.path))
    return items[:limit]


def ffmpeg_video_filter(index: int, source_duration: float, target_duration: float, width: int, height: int) -> str:
    speed = target_duration / source_duration
    return f"[{index}:v]setpts=(PTS-STARTPTS)*{speed:.8f},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[v{index}]"


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render_video(selections: list[Selection], voice_path: str, output_path: str, width: int = 1080, height: int = 1920, ffmpeg: str | None = None, subtitle_path: str | None = None, subtitle_fonts_dir: str | None = None, bgm_path: str | None = None, bgm_volume: float = 0.3, voice_volume: float = 1.0, hook_volume: float = 1.0, fps: int = 30, video_codec: str = "h264", video_bitrate: str = "5000k", audio_bitrate: str = "192k", audio_sample_rate: int = 44100, container: str = "mp4") -> str:
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
    filters.append(f"[0:a:0]apad=whole_dur={hook_duration:.6f},atrim=duration={hook_duration:.6f},asetpts=PTS-STARTPTS,aresample={audio_sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[hookaudio]")
    filters.append(f"[{voice_index}:a:0]atrim=duration={body_duration:.6f},asetpts=PTS-STARTPTS,aresample={audio_sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[bodyaudio]")
    filters.append(f"[hookaudio]volume={max(0, min(5, hook_volume)):.4f}[hookgain]")
    filters.append(f"[bodyaudio]volume={max(0, min(5, voice_volume)):.4f}[voicegain]")
    body_audio = "[voicegain]"
    bgm_index = None
    if bgm_path:
        bgm_index = len(inputs)
        inputs.append(bgm_path)
        volume = min(5.0, max(0.0, float(bgm_volume)))
        # Start music at its beginning when Body narration starts. Mixing before
        # concatenation keeps the finished Hook entirely outside the BGM track.
        filters.append(f"[{bgm_index}:a:0]atrim=duration={body_duration:.6f},asetpts=PTS-STARTPTS,volume={volume:.4f},aresample={audio_sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[bgm]")
        filters.append("[voicegain][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[bodymix]")
        body_audio = "[bodymix]"
    filters.append(f"[hookgain]{body_audio}concat=n=2:v=0:a=1,alimiter=limit=0.95:level=0:latency=1[audio]")
    final_audio = "[audio]"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y"]
    for index, item in enumerate(inputs):
        if index == bgm_index:
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", item])
    codec = {"h264": "libx264", "hevc": "libx265"}.get(video_codec.lower())
    if codec is None or container.lower() not in {"mp4", "mov"}:
        raise ValueError("不支持的输出视频编码或容器格式")
    command.extend(["-filter_complex", "; ".join(filters), "-map", final_video, "-map", final_audio, "-shortest", "-r", str(fps), "-c:v", codec, "-preset", "veryfast", "-b:v", video_bitrate, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate, "-ar", str(audio_sample_rate), "-movflags", "+faststart", output_path])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600, **text_subprocess_kwargs())
    except OSError as exc:
        raise RuntimeError("找不到 FFmpeg，请安装 FFmpeg 并将其加入 PATH。") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:] or "FFmpeg 渲染失败")
    return output_path
