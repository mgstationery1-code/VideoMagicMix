from __future__ import annotations

import os
import tempfile
import uuid
import math
import subprocess
import json
import sys
import time
import hmac
import threading
import hashlib
from dataclasses import asdict
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles

from .asr import ASRError, FasterWhisperEngine, match_body_cues, transcript_from_json, transcript_json
from .combination_previews import PreviewStore
from .excel_parser import import_workbook, preview_scheme
from .mixer import MediaInfo, Selection, _escape_filter_path, body_combination, body_combinations, body_speed_range_for_count, combination_id, complete_hook_candidates, list_videos, probe_duration, probe_media, probe_media_cached, render_video, resolve_binary
from .models import BodyConfig, Project, ScriptScheme, SubtitleStyle
from .planning import normalize_output_limit
from .rotation import MAX_BATCH, select_rotation_jobs as select_batch_jobs, summarize_rotation
from .plan_state import project_fingerprint
from .runtime import asr_worker_command, resource_root, text_subprocess_kwargs
from .subtitles import build_ass, cue_json, public_font_records, sanitize_style, split_subtitle_text, style_json

ROOT = resource_root()
FRONTEND = ROOT / "frontend"
DATA_ROOT = Path(os.getenv("VMM_DATA_DIR") or ROOT / "data")
UPLOADS = DATA_ROOT / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
PREVIEWS = DATA_ROOT / "previews"
PREVIEWS.mkdir(parents=True, exist_ok=True)
BUNDLED_FONTS = ROOT / "fonts"
app = FastAPI(title="VideoMagicMix", version="0.1.0")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
projects: dict[str, Project] = {}
transcription_tasks: dict[str, dict[str, Any]] = {}
transcription_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="videomagicmix-asr")
active_asr_processes: dict[str, subprocess.Popen[str]] = {}
runtime_state_lock = threading.Lock()
active_render_count = 0
update_lock_held = False
body_previews = PreviewStore(PREVIEWS)
render_plans: dict[str, dict[str, Any]] = {}
preflight_requests: dict[str, str] = {}
render_tasks: dict[str, dict[str, Any]] = {}


@app.middleware("http")
async def disable_local_ui_cache(request, call_next):
    """Always serve the UI bundled with the currently running desktop build."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def render_guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        global active_render_count
        with runtime_state_lock:
            if update_lock_held:
                raise HTTPException(409, "正在准备软件更新，暂时不能启动渲染")
            active_render_count += 1
        try:
            return function(*args, **kwargs)
        finally:
            with runtime_state_lock:
                active_render_count -= 1
    return guarded


def get_project(project_id: str) -> Project:
    try:
        return projects[project_id]
    except KeyError as exc:
        raise HTTPException(404, "项目不存在") from exc


def get_body_config(project: Project, scheme_id: str, row_key: str) -> BodyConfig:
    scheme_configs = project.body_configs.setdefault(scheme_id, {})
    return scheme_configs.setdefault(row_key, BodyConfig(scheme_id=scheme_id, row_key=row_key))


def _directory_part_key(value: str) -> str:
    return "".join(ch for ch in value.casefold().strip() if ch not in " \t\r\n_-()（）")


def resolve_body_group_directory(root: str, group_name: str) -> str | None:
    """Resolve a slash-delimited Excel group name below one Body root."""
    base = Path(root)
    if not base.is_dir():
        return None
    parts = [part.strip() for part in group_name.replace("\\", "/").split("/") if part.strip()]
    if not parts:
        return str(base)
    current = base
    for part in parts:
        exact = current / part
        if exact.is_dir():
            current = exact
            continue
        wanted = _directory_part_key(part)
        match = next((child for child in current.iterdir() if child.is_dir() and _directory_part_key(child.name) == wanted), None)
        if match is None:
            current = None
            break
        current = match
    if current is not None and current.is_dir():
        return str(current)

    # Also support a root whose nested path was supplied with a different
    # capitalization or where the workbook names only the final folder.
    wanted_parts = [_directory_part_key(part) for part in parts]
    for candidate in (path for path in base.rglob("*") if path.is_dir()):
        relative = [_directory_part_key(part) for part in candidate.relative_to(base).parts]
        if relative == wanted_parts or (len(parts) == 1 and relative and relative[-1] == wanted_parts[0]):
            return str(candidate)
    return None


def refresh_body_directory_mappings(project: Project) -> None:
    """Populate row-key mappings from the single configured Body root."""
    if not project.body_root_dir or not Path(project.body_root_dir).is_dir():
        return
    seen: set[str] = set()
    for scheme in project.schemes:
        for segment in scheme.segments:
            if segment.row_key.lower() == "hook" or segment.row_key.lower() in seen:
                continue
            seen.add(segment.row_key.lower())
            resolved = resolve_body_group_directory(project.body_root_dir, segment.group_name)
            if resolved:
                project.body_mapping[segment.row_key] = resolved
            else:
                project.body_mapping.pop(segment.row_key, None)


def body_config_json(config: BodyConfig) -> dict[str, Any]:
    return asdict(config)


def refresh_body_config(project: Project, scheme: ScriptScheme, segment, changed_by: str | None = None) -> tuple[BodyConfig, list[MediaInfo], list[Any]]:
    config = get_body_config(project, scheme.scheme_id, segment.row_key)
    folder = project.body_mapping.get(segment.row_key)
    target = segment.duration_seconds or 0
    if not folder or not target:
        config.target_duration = target or None
        config.scanned_video_count = 0
        config.actual_candidate_count = 0
        config.candidate_count = 0
        config.recommended_threshold = None
        return config, [], []
    paths = list_videos(folder)
    infos = [probe_media_cached(path) for path in paths]
    config.target_duration = target
    config.scanned_video_count = len(paths)
    if not config.user_configured:
        config.min_speed, config.threshold = 0.9, 1.4
    if changed_by == "candidate_count":
        recommended = body_speed_range_for_count(infos, target, max(1, config.candidate_count), config.excluded_combinations)
        if recommended is not None:
            config.min_speed, config.threshold = recommended
    config.recommended_threshold = config.threshold
    combinations = body_combinations(infos, target, config.threshold, min_speed=config.min_speed)
    if config.excluded_combinations:
        excluded = set(config.excluded_combinations)
        combinations = [item for item in combinations if combination_id(item) not in excluded]
    config.actual_candidate_count = len(combinations)
    config.candidate_count = config.actual_candidate_count
    return config, infos, combinations


def _body_segments(scheme: ScriptScheme) -> list[Any]:
    return [segment for segment in scheme.segments if segment.enabled and segment.row_key.lower() != "hook"]


def refresh_scheme_body_configs(project: Project, scheme: ScriptScheme) -> None:
    """Refresh enabled Body duration/candidate data after ASR or a manual retry."""
    for segment in _body_segments(scheme):
        refresh_body_config(project, scheme, segment)


def cancel_scheme_transcription(project_id: str, scheme_id: str, message: str) -> None:
    for task_id, task in transcription_tasks.items():
        if task.get("project_id") != project_id or task.get("scheme_id") != scheme_id or task.get("status") not in {"queued", "running"}:
            continue
        process = active_asr_processes.get(task_id)
        if process and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        task.update(status="failed", message=message, error=message, finished_at=time.time())


def _apply_transcript(project: Project, scheme: ScriptScheme, transcript: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments = _body_segments(scheme)
    for segment in scheme.segments:
        segment.start_seconds = None
        segment.end_seconds = None
        segment.subtitle_cues = []
    matches, failures = match_body_cues(
        [(segment.row_key, segment.text) for segment in segments],
        transcript,
        project.subtitle_style.max_chars_per_cue,
    )
    matched_by_index = {item["index"]: item for item in matches}
    audio_duration = probe_duration(scheme.voice_path) if scheme.voice_path else (transcript[-1].end if transcript else 0)
    for index, segment in enumerate(segments):
        item = matched_by_index.get(index)
        if not item:
            continue
        next_item = next((matched_by_index[next_index] for next_index in range(index + 1, len(segments)) if next_index in matched_by_index), None)
        segment.start_seconds = 0.0 if index == 0 else float(item["start"])
        segment.end_seconds = float(next_item["start"]) if next_item else float(audio_duration)
        segment.subtitle_cues = item["subtitle_cues"]
    return matches, failures


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "name": "VideoMagicMix", "asr": "local faster-whisper", "instance_token": os.getenv("VMM_INSTANCE_TOKEN", "")}


@app.post("/api/runtime/update-lock")
def manage_update_lock(action: str = Form(...), token: str = Form(...)) -> dict[str, Any]:
    global update_lock_held
    expected = os.getenv("VMM_INSTANCE_TOKEN", "")
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "更新请求未通过本机应用校验")
    with runtime_state_lock:
        if action == "acquire":
            if update_lock_held:
                raise HTTPException(409, "已有更新操作正在进行")
            if active_render_count or any(task["status"] in {"queued", "running"} for task in body_previews.tasks.values()):
                raise HTTPException(409, "当前正在渲染，请等待渲染完成后再更新")
            if any(task.get("status") in {"queued", "running"} for task in transcription_tasks.values()):
                raise HTTPException(409, "当前正在识别配音，请等待识别完成后再更新")
            update_lock_held = True
            return {"locked": True}
        if action == "release":
            update_lock_held = False
            return {"locked": False}
        raise HTTPException(400, "未知的更新锁操作")


@app.get("/api/fonts")
def fonts() -> dict[str, Any]:
    records = public_font_records(BUNDLED_FONTS)
    return {"fonts": records, "count": len(records), "chinese_count": sum(1 for item in records if item["supports_chinese"])}


@app.post("/api/projects/{project_id}/subtitle-style")
def update_subtitle_style(
    project_id: str,
    font_name: str = Form(""),
    font_size: int = Form(52),
    bold: bool = Form(True),
    primary_color: str = Form("#FFFFFF"),
    outline_color: str = Form("#000000"),
    outline_width: int = Form(3),
    shadow: int = Form(1),
    alignment: int = Form(2),
    margin_vertical: int = Form(120),
    max_width: int = Form(90),
    max_chars_per_cue: int = Form(10),
) -> dict[str, Any]:
    project = get_project(project_id)
    project.subtitle_style = sanitize_style(SubtitleStyle(font_name=font_name, font_size=font_size, bold=bold, primary_color=primary_color, outline_color=outline_color, outline_width=outline_width, shadow=shadow, alignment=alignment, margin_vertical=margin_vertical, max_width=max_width, max_chars_per_cue=max_chars_per_cue), BUNDLED_FONTS)
    for scheme in project.schemes:
        if scheme.transcript and scheme.voice_path:
            matches, failures = _apply_transcript(project, scheme, transcript_from_json(scheme.transcript))
            scheme.match_status = "matched" if not failures else "failed"
            scheme.error = None if not failures else "；".join(f"{failure['row_key']}：{failure['reason']}" for failure in failures)
    return project_json(project)


def _preview_source(project: Project, scheme: ScriptScheme, row_key: str) -> tuple[str | None, Any | None]:
    requested = next((segment for segment in scheme.segments if segment.row_key == row_key and segment.enabled), None)
    if requested and requested.row_key.lower() != "hook":
        folder = project.body_mapping.get(requested.row_key, "")
        paths = list_videos(folder)
        if paths:
            return paths[0], requested
    for segment in scheme.segments:
        if not segment.enabled or segment.row_key.lower() == "hook":
            continue
        folder = project.body_mapping.get(segment.row_key, "")
        paths = list_videos(folder)
        if paths:
            return paths[0], segment
    return None, requested


@app.get("/api/projects/{project_id}/subtitle-preview")
def subtitle_preview(project_id: str, scheme_id: str = "", row_key: str = "Body1", cue_index: int = 0) -> FileResponse:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id) if scheme_id else (project.schemes[0] if project.schemes else None)
    if scheme is None:
        raise HTTPException(400, "没有可预览的字幕方案")
    source, segment = _preview_source(project, scheme, row_key)
    if not source:
        raise HTTPException(400, "请先选择 Body 素材目录")
    if segment and segment.subtitle_cues:
        safe_index = max(0, min(cue_index, len(segment.subtitle_cues) - 1))
        text = segment.subtitle_cues[safe_index].text
    else:
        split = split_subtitle_text(segment.text if segment else "", project.subtitle_style.max_chars_per_cue)
        safe_index = max(0, min(cue_index, len(split) - 1)) if split else 0
        text = split[safe_index] if split else "字幕预览"
    style = sanitize_style(project.subtitle_style, BUNDLED_FONTS)
    token = uuid.uuid4().hex
    ass_path = PREVIEWS / f"{token}.ass"
    image_path = PREVIEWS / f"{token}.png"
    ass_path.write_text(build_ass([(0.0, 3.0, text)], style), encoding="utf-8")
    safe_ass = _escape_filter_path(str(ass_path))
    filter_expr = f"scale=540:960:force_original_aspect_ratio=increase,crop=540:960,ass='{safe_ass}'"
    if BUNDLED_FONTS.is_dir():
        filter_expr += f":fontsdir='{_escape_filter_path(str(BUNDLED_FONTS))}'"
    command = [resolve_binary("ffmpeg.exe"), "-y", "-i", source, "-vf", filter_expr, "-frames:v", "1", "-an", str(image_path)]
    try:
        from .runtime import text_subprocess_kwargs
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, **text_subprocess_kwargs())
    except OSError as exc:
        raise HTTPException(500, "找不到 FFmpeg，无法生成字幕预览") from exc
    finally:
        ass_path.unlink(missing_ok=True)
    if result.returncode != 0 or not image_path.is_file():
        image_path.unlink(missing_ok=True)
        raise HTTPException(500, result.stderr[-2000:] or "字幕预览生成失败")
    return FileResponse(image_path, media_type="image/png", filename="subtitle-preview.png")


@app.get("/api/asr/health")
def asr_health() -> dict[str, Any]:
    from importlib.util import find_spec
    available = find_spec("faster_whisper") is not None
    engine = FasterWhisperEngine()
    cache_dir = Path(os.getenv("WHISPER_CACHE_DIR") or (ROOT / "data" / "models"))
    model_cached = bool(engine.model_path and Path(engine.model_path).exists()) or any(cache_dir.glob("**/model.bin")) if cache_dir.exists() else False
    return {"status": "ready" if available and model_cached else ("model_missing" if available else "dependency_missing"), "provider": "local faster-whisper", "model": engine.model_path or engine.model_size, "model_cached": model_cached, "cache_dir": str(cache_dir), "device": os.getenv("WHISPER_DEVICE", "cpu")}


@app.post("/api/projects/import-xlsx")
async def import_xlsx(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "project.xlsx").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(400, "只支持 .xlsx 或 .xlsm")
    target = UPLOADS / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(await file.read())
    try:
        sheet, schemes, errors, warnings = import_workbook(str(target))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    project_id = uuid.uuid4().hex
    project = Project(project_id, Path(file.filename or "project.xlsx").stem, str(target), sheet, schemes=schemes, errors=errors, warnings=warnings)
    projects[project_id] = project
    return project_json(project)


@app.get("/api/projects/{project_id}")
def read_project(project_id: str) -> dict[str, Any]:
    return project_json(get_project(project_id))


@app.post("/api/projects/{project_id}/settings")
def settings(project_id: str, hook_dir: str = Form(""), body_root_dir: str = Form(""), output_dir: str = Form(""), body_threshold: float = Form(1.15), output_limit: str = Form(""), bgm_volume: float = Form(0.3), voice_volume: float = Form(1), hook_volume: float = Form(1), max_overlap: str = Form(""), recent_window: int = Form(3), output_format: str = Form(""), output_video_codec: str = Form(""), output_resolution: str = Form(""), output_fps: int = Form(0), output_video_bitrate: str = Form(""), output_audio_bitrate: str = Form(""), output_audio_sample_rate: int = Form(0)) -> dict[str, Any]:
    project = get_project(project_id)
    try:
        normalized_limit = normalize_output_limit(output_limit)
        if normalized_limit is not None and normalized_limit > MAX_BATCH:
            raise ValueError(f"每批最多 {MAX_BATCH} 条，请分批生成")
        if not 1 <= recent_window <= 10:
            raise ValueError("关注最近条数必须是 1–10 的整数")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if any(not math.isfinite(value) or not 0 <= value <= 5 for value in (bgm_volume, voice_volume, hook_volume)):
        raise HTTPException(400, "音量必须介于 0–500%")
    try:
        overlap = float(max_overlap) / 100 if max_overlap.strip() else None
        if overlap is not None and (not math.isfinite(overlap) or not 0 <= overlap <= 1):
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(400, "Body 复用率目标必须是 0–100，留空表示只报告不判定") from exc
    previous_root = project.body_root_dir
    project.hook_dir, project.body_root_dir, project.output_dir = hook_dir.strip(), body_root_dir.strip(), output_dir.strip()
    if project.body_root_dir and project.body_root_dir != previous_root:
        refresh_body_directory_mappings(project)
    project.output_limit = normalized_limit
    project.bgm_volume, project.voice_volume, project.hook_volume = bgm_volume, voice_volume, hook_volume
    project.max_overlap = overlap
    project.recent_window = recent_window
    output_format = output_format.strip().lower() or project.output_format
    output_video_codec = output_video_codec.strip().lower() or project.output_video_codec
    output_resolution = output_resolution.strip() or project.output_resolution
    output_fps = output_fps or project.output_fps
    output_video_bitrate = output_video_bitrate.strip() or project.output_video_bitrate
    output_audio_bitrate = output_audio_bitrate.strip() or project.output_audio_bitrate
    output_audio_sample_rate = output_audio_sample_rate or project.output_audio_sample_rate
    if output_format not in {"mp4", "mov"} or output_video_codec not in {"h264", "hevc"}:
        raise HTTPException(400, "输出格式只支持 MP4/MOV，视频编码只支持 H.264/HEVC")
    if output_resolution not in {"1080x1920", "2160x3840"} or output_fps not in {24, 25, 30, 50, 60} or output_audio_sample_rate not in {44100, 48000}:
        raise HTTPException(400, "输出分辨率、帧率或采样率不受支持")
    for bitrate in (output_video_bitrate, output_audio_bitrate):
        if not bitrate.strip().lower().endswith(("k", "m")):
            raise HTTPException(400, "码率请使用例如 5000k、10M 的格式")
    project.output_format, project.output_video_codec, project.output_resolution = output_format.lower(), output_video_codec.lower(), output_resolution
    project.output_fps, project.output_video_bitrate = output_fps, output_video_bitrate.strip()
    project.output_audio_bitrate, project.output_audio_sample_rate = output_audio_bitrate.strip(), output_audio_sample_rate
    return project_json(project)


@app.post("/api/projects/{project_id}/bgm")
async def upload_bgm(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    project = get_project(project_id)
    suffix = Path(file.filename or "bgm.bin").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
        raise HTTPException(400, "BGM 格式必须是 mp3/wav/m4a/aac/flac/ogg/opus")
    content = await file.read()
    if not content:
        raise HTTPException(400, "BGM 文件为空")
    target = UPLOADS / f"{project_id}_bgm{suffix}"
    target.write_bytes(content)
    try:
        info = probe_media(str(target))
        if not info.has_audio:
            raise HTTPException(400, "所选文件没有有效音轨")
    except RuntimeError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, f"无法读取 BGM 文件：{exc}") from exc
    project.bgm_path = str(target)
    return project_json(project)


@app.post("/api/projects/{project_id}/bgm/clear")
def clear_bgm(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    project.bgm_path = ""
    return project_json(project)


@app.post("/api/projects/{project_id}/audio-preview")
def audio_preview(project_id: str, scheme_id: str = Form(...), row_key: str = Form("")) -> FileResponse:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    is_hook = row_key.lower() == "hook"
    hooks = list_videos(project.hook_dir) if project.hook_dir else []
    source_path = hooks[0] if is_hook and hooks else scheme.voice_path
    if is_hook and not hooks:
        raise HTTPException(400, "请先选择有效的 Hook 素材")
    if not source_path or not Path(source_path).is_file():
        raise HTTPException(400, "当前字幕方案还没有可试听的配音")
    try:
        total_duration = probe_duration(source_path)
        start = 0.0
        duration = total_duration if is_hook else min(12.0, total_duration)
        if row_key and not is_hook:
            segment = next((item for item in _body_segments(scheme) if item.row_key == row_key), None)
            if not segment or segment.start_seconds is None or not segment.duration_seconds:
                raise HTTPException(400, "该段文案尚未匹配配音时间，请先完成识别")
            start = segment.start_seconds
            duration = segment.duration_seconds
        if duration <= 0:
            raise ValueError("音频时长无效")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, f"无法生成音频试听：{exc}") from exc
    preview_path = PREVIEWS / f"{project_id}-{scheme_id}-{uuid.uuid4().hex}.wav"
    # Music starts with Body, so its phase depends only on narration time.
    music_start = start
    gain = project.hook_volume if is_hook else project.voice_volume
    filters = [f"[0:a:0]atrim=start={start:.6f}:duration={duration:.6f},asetpts=PTS-STARTPTS,volume={gain:.4f},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[voice]"]
    inputs = ["-i", source_path]
    if project.bgm_path and not is_hook:
        inputs += ["-stream_loop", "-1", "-i", project.bgm_path]
        filters += [f"[1:a:0]atrim=start={music_start:.6f}:duration={duration:.6f},asetpts=PTS-STARTPTS,volume={project.bgm_volume:.4f},aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[bgm]",
                    "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[dialogue]"]
        source = "[dialogue]"
    else:
        source = "[voice]"
    filters.append(source + "alimiter=limit=0.95:level=0:latency=1[mix]")
    command = [
        resolve_binary("ffmpeg.exe"), "-y", *inputs, "-filter_complex", ";".join(filters),
        "-map", "[mix]", "-t", f"{duration:.6f}", "-c:a", "pcm_s16le", str(preview_path),
    ]
    try:
        from .runtime import text_subprocess_kwargs
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, **text_subprocess_kwargs())
        if result.returncode != 0 or not preview_path.is_file():
            raise RuntimeError(result.stderr[-2000:] or "FFmpeg 未生成试听音频")
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        preview_path.unlink(missing_ok=True)
        raise HTTPException(500, f"生成音频试听失败：{exc}") from exc
    return FileResponse(preview_path, media_type="audio/wav", filename="VideoMagicMix-audio-preview.wav", background=BackgroundTask(preview_path.unlink, missing_ok=True))


@app.post("/api/projects/{project_id}/mapping/body")
def map_body_directory(project_id: str, group_key: str = Form(...), directory: str = Form("")) -> dict[str, Any]:
    """Save one Body row's independent source directory."""
    project = get_project(project_id)
    key = group_key.strip()
    if not key or key.lower() == "hook":
        raise HTTPException(400, "无效的 Body 行标识")
    known = {segment.row_key for scheme in project.schemes for segment in scheme.segments if segment.row_key.lower() != "hook"}
    if key not in known:
        raise HTTPException(404, f"未找到 Body 行：{key}")
    value = directory.strip()
    if value and not Path(value).is_dir():
        raise HTTPException(400, f"Body 目录不存在：{value}")
    if value and project.body_root_dir and Path(value).resolve() == Path(project.body_root_dir).resolve():
        segment = next((item for scheme in project.schemes for item in scheme.segments if item.row_key == key), None)
        resolved = resolve_body_group_directory(project.body_root_dir, segment.group_name if segment else "")
        if resolved:
            value = resolved
    if value:
        project.body_mapping[key] = value
    else:
        project.body_mapping.pop(key, None)
    return project_json(project)


@app.post("/api/projects/{project_id}/body-config")
def update_body_config(project_id: str, scheme_id: str = Form(...), row_key: str = Form(...), threshold: str = Form(""), candidate_count: str = Form(""), changed_by: str = Form("threshold"), min_speed: str = Form("0.9")) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    segment = next((item for item in scheme.segments if item.row_key == row_key), None)
    if segment is None or segment.row_key.lower() == "hook":
        raise HTTPException(404, f"未找到 Body 行：{row_key}")
    if not segment.enabled:
        raise HTTPException(400, f"{row_key} 文案为空，已跳过")
    if changed_by not in {"threshold", "candidate_count"}:
        raise HTTPException(400, "changed_by 必须是 threshold 或 candidate_count")
    config = get_body_config(project, scheme_id, row_key)
    if changed_by == "threshold":
        try:
            value = float(threshold)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Body 阈值必须是数字") from exc
        try:
            low = float(min_speed)
        except ValueError as exc:
            raise HTTPException(400, "最低倍速必须是正数") from exc
        if not math.isfinite(value) or not math.isfinite(low) or low <= 0 or value < low:
            raise HTTPException(400, "倍速必须是有限正数，最低倍速不能高于最高倍速")
        config.min_speed = low
        config.threshold = value
    else:
        try:
            value = int(candidate_count)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "候选数量必须是正整数") from exc
        if value < 1:
            raise HTTPException(400, "候选数量必须是正整数")
        config.candidate_count = value
    config.user_configured = True
    config.control_mode = changed_by
    config, _, _ = refresh_body_config(project, scheme, segment, changed_by=changed_by)
    return {"config": body_config_json(config), "project": project_json(project)}


@app.get("/api/projects/{project_id}/schemes/{scheme_id}/bodies/{row_key}/combinations")
def preview_body_combinations(project_id: str, scheme_id: str, row_key: str, scope: str = "current", offset: int = 0, page_size: int = 100) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    segment = next((item for item in scheme.segments if item.row_key == row_key), None)
    if segment is None or segment.row_key.lower() == "hook":
        raise HTTPException(404, f"未找到 Body 行：{row_key}")
    if not segment.enabled:
        raise HTTPException(400, f"{row_key} 文案为空，已跳过")
    folder = project.body_mapping.get(row_key)
    if not folder or not Path(folder).is_dir():
        raise HTTPException(400, f"{row_key} 尚未选择有效的视频目录")
    if not segment.duration_seconds:
        raise HTTPException(409, f"{row_key} 尚无配音目标时长，请先完成配音识别")

    if scope not in {"current", "all"}:
        raise HTTPException(400, "scope 必须是 current 或 all")
    config, infos, combinations = refresh_body_config(project, scheme, segment)
    # Include unchecked combinations so users can select them again.
    combinations = body_combinations(infos, segment.duration_seconds,
                                    float("inf") if scope == "all" else config.threshold,
                                    min_speed=0.000001 if scope == "all" else config.min_speed, max_results=70000)
    if offset < 0 or not 1 <= page_size <= 200:
        raise HTTPException(400, "分页参数无效")
    total = len(combinations)
    root = Path(folder)
    entries = []
    for selection in combinations[offset:offset + page_size]:
        identity = combination_id(selection)
        speed = selection.source_duration / selection.target_duration
        in_range = config.min_speed - 1e-9 <= speed <= config.threshold + 1e-9
        key = body_previews.ready(selection)
        entries.append({
            "id": identity,
            "selected": in_range and identity not in config.excluded_combinations,
            "preview_url": f"/api/combination-previews/{key}" if key else None,
            "files": [Path(item.path).relative_to(root).as_posix() for item in selection.files],
            "source_duration": selection.source_duration,
            "target_duration": selection.target_duration,
            "playback_speed": selection.source_duration / selection.target_duration if selection.target_duration else 1.0,
            "required_threshold": selection.source_duration / selection.target_duration if selection.target_duration else 1.0,
            "within_current_threshold": in_range,
        })
    return {
        "scheme_id": scheme_id,
        "row_key": row_key,
        "target_duration": segment.duration_seconds,
        "threshold": config.threshold,
        "candidate_count": total,
        "selected_count": config.actual_candidate_count,
        "min_speed": config.min_speed,
        "offset": offset,
        "next_offset": offset + page_size if offset + page_size < total else None,
        "enumeration_note": "枚举距离目标时长最近的最多18个文件，每组合最多8个文件，最多返回70000个组合；固定文件顺序，不枚举排列。",
        "scope": scope,
        "scanned_video_count": len(infos),
        "combinations": entries,
    }


@app.post("/api/projects/{project_id}/schemes/{scheme_id}/bodies/{row_key}/selection")
def select_body_combination(project_id: str, scheme_id: str, row_key: str, identity: str = Form(...), selected: bool = Form(...)) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    segment = next((item for item in _body_segments(scheme) if item.row_key == row_key), None)
    if segment is None:
        raise HTTPException(404, "未找到启用的 Body")
    config, infos, _ = refresh_body_config(project, scheme, segment)
    options = body_combinations(infos, segment.duration_seconds or 0, config.threshold, min_speed=config.min_speed)
    if not any(combination_id(item) == identity for item in options):
        raise HTTPException(400, "组合已不在当前倍速范围内，请刷新列表")
    excluded = set(config.excluded_combinations)
    if selected:
        excluded.discard(identity)
    else:
        excluded.add(identity)
    config.excluded_combinations = sorted(excluded)
    refresh_body_config(project, scheme, segment)
    return {"config": body_config_json(config)}


@app.post("/api/projects/{project_id}/schemes/{scheme_id}/bodies/{row_key}/pre-render")
@render_guard
def prerender_body(project_id: str, scheme_id: str, row_key: str) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    segment = next((item for item in _body_segments(scheme) if item.row_key == row_key), None)
    if segment is None:
        raise HTTPException(404, "未找到启用的 Body")
    config, _, options = refresh_body_config(project, scheme, segment)
    if not options:
        raise HTTPException(400, "没有勾选的可用组合，请先选择组合")
    def still_enabled(item):
        speed = item.source_duration / item.target_duration
        return (combination_id(item) not in config.excluded_combinations
                and config.min_speed - 1e-9 <= speed <= config.threshold + 1e-9
                and segment.duration_seconds == item.target_duration)
    return body_previews.start(f"{project_id}/{scheme_id}/{row_key}", options, still_enabled)


@app.get("/api/preview-tasks/{task_id}")
def read_preview_task(task_id: str) -> dict[str, Any]:
    if task_id not in body_previews.tasks:
        raise HTTPException(404, "预渲染任务不存在")
    return body_previews.tasks[task_id]


@app.get("/api/combination-previews/{key}")
def read_combination_preview(key: str) -> FileResponse:
    path = body_previews.assets.get(key)
    if path is None or not path.is_file():
        raise HTTPException(404, "尚未预渲染该组合，请点击预渲染片段")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/projects/{project_id}/schemes/{scheme_id}/voice")
async def upload_voice(project_id: str, scheme_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    suffix = Path(file.filename or "voice.bin").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
        raise HTTPException(400, "配音格式必须是 mp3/wav/m4a/aac/flac/ogg/opus")
    target = UPLOADS / f"{project_id}_{scheme_id}{suffix}"
    target.write_bytes(await file.read())
    cancel_scheme_transcription(project_id, scheme_id, "已由新上传的配音替换，旧识别任务已取消")
    scheme.voice_path = str(target)
    scheme.asr_status, scheme.match_status, scheme.error = "uploaded", "not_started", None
    return scheme_json(scheme)


def _run_transcription(task_id: str, project_id: str, scheme_id: str) -> None:
    task = transcription_tasks[task_id]
    started_at = time.time()
    task.update(status="running", message="正在加载本地 Whisper 模型", started_at=started_at, elapsed_seconds=0)
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    result_path = Path(tempfile.gettempdir()) / f"videomagicmix-asr-{task_id}.json"
    try:
        audio_duration = probe_duration(scheme.voice_path)
        timeout_seconds = min(1800, max(180, int(audio_duration * 8)))
        env = os.environ.copy()
        env.setdefault("WHISPER_DEVICE", "cpu")
        command = asr_worker_command(scheme.voice_path, str(result_path))
        process = subprocess.Popen(command, cwd=str(ROOT), env=env, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        active_asr_processes[task_id] = process
        while process.poll() is None:
            elapsed = int(time.time() - started_at)
            task.update(message=f"正在识别配音，已用时 {elapsed} 秒", elapsed_seconds=elapsed, timeout_seconds=timeout_seconds)
            if elapsed >= timeout_seconds:
                process.kill()
                process.wait(timeout=10)
                raise ASRError(f"配音识别超过 {timeout_seconds} 秒，任务已自动停止，请重新识别")
            time.sleep(1)
        if not result_path.is_file():
            raise ASRError(f"识别工作进程异常退出（代码 {process.returncode}），请重新识别")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not payload.get("ok"):
            raise ASRError(payload.get("error") or "本地 Whisper 识别失败")
        transcript = transcript_from_json(payload.get("transcript", []))
        if not transcript:
            raise ASRError("配音中没有识别到有效中文，请检查音量和音频内容")
        task.update(message="语音识别完成，正在匹配 Body 文案", elapsed_seconds=int(time.time() - started_at))
        matches, failures = _apply_transcript(project, scheme, transcript)
        refresh_scheme_body_configs(project, scheme)
    except ASRError as exc:
        scheme.asr_status, scheme.match_status, scheme.error = "error", "error", str(exc)
        task.update(status="failed", message=str(exc), error=str(exc), elapsed_seconds=int(time.time() - started_at))
        return
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        scheme.asr_status, scheme.match_status, scheme.error = "error", "error", str(exc)
        task.update(status="failed", message=str(exc), error=str(exc), elapsed_seconds=int(time.time() - started_at))
        return
    finally:
        active_asr_processes.pop(task_id, None)
        result_path.unlink(missing_ok=True)
    scheme.transcript = transcript_json(transcript)
    scheme.asr_status = "completed"
    scheme.match_status = "matched" if not failures else "failed"
    scheme.error = None if not failures else "；".join(f"{failure['row_key']}：{failure['reason']}（Excel：{failure.get('expected', '')}；ASR：{failure.get('recognized', '')}；时间：{failure.get('time_range')}）" for failure in failures)
    task.update(status="completed" if not failures else "failed", message="识别完成" if not failures else "文案匹配失败", error=scheme.error, elapsed_seconds=int(time.time() - started_at), result={**scheme_json(scheme), "failures": failures, "matches": matches})


@app.post("/api/projects/{project_id}/schemes/{scheme_id}/transcribe")
def transcribe(project_id: str, scheme_id: str) -> dict[str, Any]:
    with runtime_state_lock:
        if update_lock_held:
            raise HTTPException(409, "正在准备软件更新，暂时不能启动配音识别")
        return _queue_transcription(project_id, scheme_id)


@app.post("/api/projects/{project_id}/schemes/{scheme_id}/transcribe/retry")
def retry_transcription(project_id: str, scheme_id: str) -> dict[str, Any]:
    with runtime_state_lock:
        if update_lock_held:
            raise HTTPException(409, "正在准备软件更新，暂时不能启动配音识别")
        cancel_scheme_transcription(project_id, scheme_id, "旧识别任务已取消，正在重新识别")
        return _queue_transcription(project_id, scheme_id)


def _queue_transcription(project_id: str, scheme_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    if not scheme.voice_path:
        raise HTTPException(400, "请先上传该字幕方案的完整配音")
    active = next((item for item in transcription_tasks.values() if item.get("project_id") == project_id and item.get("scheme_id") == scheme_id and item.get("status") in {"queued", "running"}), None)
    if active:
        return active
    task_id = uuid.uuid4().hex
    transcription_tasks[task_id] = {"task_id": task_id, "project_id": project_id, "scheme_id": scheme_id, "status": "queued", "message": "等待识别", "error": None, "created_at": time.time(), "elapsed_seconds": 0}
    transcription_executor.submit(_run_transcription, task_id, project_id, scheme_id)
    return transcription_tasks[task_id]


@app.get("/api/projects/{project_id}/schemes/{scheme_id}")
def read_scheme(project_id: str, scheme_id: str) -> dict[str, Any]:
    return scheme_json(get_project(project_id).scheme(scheme_id))


@app.get("/api/tasks/{task_id}")
def read_task(task_id: str) -> dict[str, Any]:
    try:
        return transcription_tasks[task_id]
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc


@app.post("/api/projects/{project_id}/preflight")
def preflight(project_id: str, scheme_id: str | None = None) -> dict[str, Any]:
    project = get_project(project_id)
    request_id = uuid.uuid4().hex
    with runtime_state_lock:
        if render_plans.get(project_id, {}).get("status") == "rendering":
            raise HTTPException(409, "正在渲染已确认的组合，请等待本批次结束后再预检")
        preflight_requests[project_id] = request_id
        render_plans.pop(project_id, None)
    refresh_body_directory_mappings(project)
    fingerprint = project_fingerprint(project)
    failures = list(project.errors)
    if not project.hook_dir:
        failures.append("未设置 Hook 目录")
    if not project.schemes:
        failures.append("没有字幕方案")
    if scheme_id:
        schemes_to_check = [project.scheme(scheme_id)]
    else:
        schemes_to_check = [scheme for scheme in project.schemes if scheme.voice_path]
        if not schemes_to_check:
            failures.append("没有已上传配音的字幕方案")
    for scheme in schemes_to_check:
        if not scheme.voice_path:
            failures.append(f"{scheme.display_name} 未上传配音")
        if scheme.match_status != "matched":
            details = f"：{scheme.error}" if scheme.error else ""
            failures.append(f"{scheme.display_name} 尚未通过字幕/配音匹配{details}")
        if not _body_segments(scheme):
            failures.append(f"{scheme.display_name} 没有启用的 Body 文案")
    if project.hook_dir and not Path(project.hook_dir).is_dir():
        failures.append(f"Hook 目录不存在：{project.hook_dir}")
    if project.body_root_dir and not Path(project.body_root_dir).is_dir():
        failures.append(f"Body 根目录不存在：{project.body_root_dir}")
    if project.output_dir and not Path(project.output_dir).is_dir():
        failures.append(f"输出目录不存在：{project.output_dir}")
    bgm_info = None
    if project.bgm_path:
        if not Path(project.bgm_path).is_file():
            failures.append(f"BGM 文件不存在：{project.bgm_path}")
        else:
            try:
                bgm_info = probe_media(project.bgm_path)
                if not bgm_info.has_audio:
                    failures.append(f"BGM 文件没有有效音轨：{project.bgm_path}")
            except RuntimeError as exc:
                failures.append(str(exc))
    hook_info: list[MediaInfo] = []
    for path in (list_videos(project.hook_dir) if project.hook_dir else []):
        try:
            info = probe_media_cached(path)
            if not info.has_audio:
                failures.append(f"Hook 缺少有效音轨：{path}")
            else:
                hook_info.append(info)
        except RuntimeError as exc:
            failures.append(str(exc))
    if project.hook_dir and Path(project.hook_dir).is_dir() and not list_videos(project.hook_dir):
        failures.append("Hook 目录中没有视频")
    plan: list[dict[str, Any]] = []
    scheme_estimates: list[dict[str, Any]] = []
    planning_schemes = []
    row_keys = {s.scheme_id: [segment.row_key for segment in _body_segments(s)] for s in schemes_to_check}
    scanned_paths = {}
    if hook_info:
        plan.append({"row_key": "Hook", "hook_count": len(hook_info), "candidate_count": len(hook_info), "options": [item.path for item in hook_info], "message": f"已扫描 {len(hook_info)} 个 Hook，全部作为完整候选"})
        for scheme in schemes_to_check:
            candidate_counts: list[int] = [len(hook_info)]
            body_pools = []
            for segment in _body_segments(scheme):
                target = segment.duration_seconds or 0
                folder = project.body_mapping.get(segment.row_key)
                if not folder:
                    failures.append(f"{scheme.display_name}/{segment.row_key} 未匹配 Body 目录：{segment.group_name}")
                    candidate_counts.append(0)
                    continue
                config, infos, combinations = refresh_body_config(project, scheme, segment)
                scanned_paths.setdefault(scheme.scheme_id, {})[segment.row_key] = [info.path for info in infos]
                if not combinations:
                    selection = body_combination(infos, target, config.threshold, min_speed=config.min_speed)
                    reason = "当前范围的组合已全部取消勾选" if selection.accepted else selection.message
                    failures.append(f"{scheme.display_name}/{segment.row_key}：{reason}")
                body_pools.append(combinations)
                candidate_counts.append(len(combinations))
                plan.append({"scheme_id": scheme.scheme_id, "display_name": scheme.display_name, "row_key": segment.row_key, "folder": folder, "target": target, "target_duration": target, "scanned_video_count": config.scanned_video_count, "candidate_count": config.candidate_count, "actual_candidate_count": len(combinations), "threshold": config.threshold, "min_speed": config.min_speed, "recommended_threshold": config.recommended_threshold, "files": [[f.path for f in selection.files] for selection in combinations[:5]], "speed_factor": combinations[0].speed_factor if combinations else None, "subtitle_cues": [cue_json(cue) for cue in segment.subtitle_cues]})
            estimate = math.prod(candidate_counts) if candidate_counts and all(candidate_counts) else 0
            body_duration = sum(segment.duration_seconds or 0 for segment in _body_segments(scheme))
            scheme_estimates.append({"scheme_id": scheme.scheme_id, "display_name": scheme.display_name, "estimated_outputs": estimate, "candidate_counts": candidate_counts, "hook_count": len(hook_info), "body_voice_duration": body_duration})
            if estimate:
                planning_schemes.append((scheme.scheme_id, complete_hook_candidates(hook_info), body_pools))
    total_estimate = sum(item["estimated_outputs"] for item in scheme_estimates)
    planned_outputs = 0
    overlap_report = None
    jobs = []
    if not failures:
        jobs, overlap_report = select_batch_jobs(planning_schemes, project.output_limit, project.max_overlap, project.recent_window)
        # Attach real row keys (including skipped Body rows) and scan diagnostics.
        audited = summarize_rotation(jobs, project.recent_window, project.max_overlap, row_keys, scanned_paths)
        overlap_report.update(audited)
        for group in overlap_report.get("groups", []):
            ordinal = int(group["row_key"][4:]) - 1
            group["row_key"] = row_keys[group["scheme_id"]][ordinal]
        overlap_report["preview_jobs"] = [{"number": i+1, "scheme_id": job.scheme_id,
            "hook": job.selections[0].files[0].path,
            "bodies": [{"row_key": key, "files": [f.path for f in selection.files],
                         "duration": selection.target_duration, "speed": selection.source_duration / selection.target_duration}
                        for key, selection in zip(row_keys[job.scheme_id], job.selections[1:])]}
            for i, job in enumerate(jobs[:10])]
        planned_outputs = len(jobs)
    for item in scheme_estimates:
        item["planned_outputs"] = sum(job.scheme_id == item["scheme_id"] for job in jobs)
    snapshot_project = deepcopy(project)
    if project_fingerprint(snapshot_project) != fingerprint or project_fingerprint(project) != fingerprint:
        failures.append("预检期间配置或素材已改变，请重新预检")
    plan_id = uuid.uuid4().hex if not failures and jobs else None
    with runtime_state_lock:
        if preflight_requests.get(project_id) != request_id:
            failures.append("已有更新的预检请求，本次结果已过期")
            plan_id = None
        elif plan_id:
            render_plans[project_id] = {
                "plan_id": plan_id, "status": "ready", "fingerprint": fingerprint,
                "project": snapshot_project, "scheme_id": scheme_id or "",
                "jobs": deepcopy(jobs), "overlap_report": overlap_report,
                "estimated_outputs": total_estimate,
                "row_keys": row_keys,
            }
        else:
            render_plans.pop(project_id, None)
    return {
        "ok": not failures,
        "requires_acceptance": bool(overlap_report and (overlap_report["requires_acceptance"] or overlap_report["shortage"])),
        "plan_id": plan_id,
        "failures": failures,
        "plan": plan,
        "body_configs": {scheme_id: {row_key: body_config_json(config) for row_key, config in configs.items()} for scheme_id, configs in project.body_configs.items()},
        "estimated_outputs": total_estimate,
        "output_limit": project.output_limit or 0,
        "planned_outputs": planned_outputs,
        "overlap": overlap_report,
        "hook_count": len(hook_info),
        "scheme_estimates": scheme_estimates,
        "bgm": {"path": project.bgm_path, "source_duration": bgm_info.duration, "volume": project.bgm_volume, "alignment": "仅用于 Body，从 Body 配音开始播放，按配音时长裁剪或循环；Hook 不加背景音乐"} if bgm_info else None,
    }


@app.get("/api/render-tasks/{task_id}")
def render_task_status(task_id: str) -> dict[str, Any]:
    task = render_tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "渲染任务不存在")
    return dict(task)


def verify_rendered_output(path: str) -> None:
    media = probe_media(path)
    if media.duration <= 0 or not media.has_audio:
        raise RuntimeError("视频时长无效或缺少音轨")
    command = [resolve_binary("ffmpeg.exe"), "-v", "error", "-i", path, "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, **text_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"无法完整解码校验视频：{exc}") from exc
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or "完整解码校验失败")


@app.post("/api/projects/{project_id}/render")
@render_guard
def render(project_id: str, scheme_id: str = Form(""), limit: str = Form(""), plan_id: str = Form(""), task_id: str = Form(""), accept_warnings: bool = Form(False)) -> dict[str, Any]:
    current_project = get_project(project_id)
    try:
        request_limit = normalize_output_limit(limit) if limit and limit.strip() else current_project.output_limit
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with runtime_state_lock:
        snapshot = render_plans.get(project_id)
        if not plan_id or not snapshot or snapshot["plan_id"] != plan_id or snapshot["status"] != "ready":
            raise HTTPException(409, "请先预检并确认本批次的数量和重复率，再开始渲染")
        if scheme_id != snapshot["scheme_id"] or request_limit != snapshot["project"].output_limit:
            raise HTTPException(409, "渲染范围或生成数量已改变，请重新预检")
        if project_fingerprint(current_project) != snapshot["fingerprint"]:
            snapshot["status"] = "stale"
            raise HTTPException(409, "配置或素材已改变，原预检结果已失效，请重新预检")
        report = snapshot["overlap_report"]
        if (report["requires_acceptance"] or report["shortage"]) and not accept_warnings:
            raise HTTPException(409, "当前安排存在超标、重复组合或数量不足，请查看明细并明确接受当前安排后再渲染")
        snapshot["status"] = "rendering"
    project = snapshot["project"]
    overlap_report = snapshot["overlap_report"]
    selected_jobs = snapshot["jobs"]
    output_budget = len(selected_jobs)
    task_id = task_id or uuid.uuid4().hex
    render_tasks[task_id] = {"task_id": task_id, "project_id": project_id, "status": "running", "total": output_budget, "completed": 0, "healthy_count": 0, "current_file": "", "outputs": []}
    outputs: list[str] = []
    output_dir = Path(project.output_dir or (Path(project.excel_path).parent / "VideoMagicMix-输出"))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for job in selected_jobs:
            if len(outputs) >= output_budget:
                break
            scheme = project.scheme(job.scheme_id)
            selections = job.selections
            hook = selections[0]
            output_number = len(outputs) + 1
            ass_path = output_dir / f".{project.project_id}-{scheme.scheme_id}-{output_number}.ass"
            hook_duration = hook.source_duration
            subtitle_events = [
                (hook_duration + float(cue.start_seconds), hook_duration + float(cue.end_seconds), cue.text)
                for segment in _body_segments(scheme)
                for cue in segment.subtitle_cues
                if cue.matched and cue.start_seconds is not None and cue.end_seconds is not None
            ]
            ass_path.write_text(build_ass(subtitle_events, sanitize_style(project.subtitle_style, BUNDLED_FONTS)), encoding="utf-8")
            output_path = output_dir / f"{project.project_name}-{scheme.display_name}-{output_number:03d}.{project.output_format}"
            render_tasks[task_id].update(current_file=output_path.name, completed=len(outputs), outputs=list(outputs))
            try:
                width, height = (int(value) for value in project.output_resolution.split("x", 1))
                render_video(selections, scheme.voice_path, str(output_path), width=width, height=height, fps=project.output_fps, video_codec=project.output_video_codec, video_bitrate=project.output_video_bitrate, audio_bitrate=project.output_audio_bitrate, audio_sample_rate=project.output_audio_sample_rate, container=project.output_format, subtitle_path=str(ass_path), subtitle_fonts_dir=str(BUNDLED_FONTS) if BUNDLED_FONTS.is_dir() else None, bgm_path=project.bgm_path or None, bgm_volume=project.bgm_volume, voice_volume=project.voice_volume, hook_volume=project.hook_volume)
                if Path(output_path).is_file():
                    verify_rendered_output(str(output_path))
            except Exception as exc:
                generated = len(outputs)
                completed_report = summarize_rotation(selected_jobs[:generated], project.recent_window, project.max_overlap, snapshot["row_keys"])
                render_tasks[task_id].update(status="failed", completed=generated, healthy_count=generated, error=f"{output_path.name}：{exc}", remaining=max(0, output_budget - generated), outputs=list(outputs), overlap=completed_report)
                raise HTTPException(500, {"message": str(exc), "failed_index": output_number, "generated_count": generated, "remaining_count": max(0, output_budget - generated), "outputs": outputs, "overlap": completed_report}) from exc
            finally:
                ass_path.unlink(missing_ok=True)
            outputs.append(str(output_path))
            render_tasks[task_id].update(completed=len(outputs), healthy_count=len(outputs), outputs=list(outputs))
    except Exception:
        snapshot["status"] = "failed"
        render_tasks[task_id].update(status="failed", completed=len(outputs), healthy_count=len(outputs), outputs=list(outputs))
        raise
    # Test doubles may not create files; real files have already passed FFprobe
    # and a full FFmpeg decode before being included in the task count.
    healthy_outputs: list[str] = []
    health_failures: list[str] = []
    for output in outputs:
        if not Path(output).is_file():
            healthy_outputs.append(output)
            continue
        try:
            media = probe_media(output)
            if media.duration <= 0:
                raise RuntimeError("视频时长无效")
            healthy_outputs.append(output)
        except Exception as exc:
            health_failures.append(f"{output}：{exc}")
    snapshot["status"] = "completed" if not health_failures else "failed"
    if health_failures:
        successful_jobs = [job for job, output in zip(selected_jobs, outputs) if output in healthy_outputs]
        overlap_report = summarize_rotation(successful_jobs, project.recent_window, project.max_overlap, snapshot["row_keys"])
    render_tasks[task_id].update(status=snapshot["status"], completed=len(healthy_outputs), healthy_count=len(healthy_outputs), current_file="", outputs=list(healthy_outputs), error="；".join(health_failures))
    return {"ok": not health_failures, "plan_id": plan_id, "scheme_id": scheme_id or None, "outputs": healthy_outputs, "output": healthy_outputs[0] if healthy_outputs else None, "count": len(healthy_outputs), "healthy_count": len(healthy_outputs), "health_failures": health_failures, "estimated_outputs": snapshot["estimated_outputs"], "planned_outputs": len(outputs), "output_limit": request_limit or 0, "overlap": overlap_report}


def scheme_json(scheme) -> dict[str, Any]:
    return {"scheme_id": scheme.scheme_id, "source_column": scheme.source_column, "display_name": scheme.display_name, "voice_path": scheme.voice_path, "asr_status": scheme.asr_status, "match_status": scheme.match_status, "error": scheme.error, "preview": preview_scheme(scheme), "segments": [{"row_key": s.row_key, "group_name": s.group_name, "text": s.text, "start": s.start_seconds, "end": s.end_seconds, "duration": s.duration_seconds, "enabled": s.enabled, "skip_reason": s.skip_reason, "subtitle_cues": [cue_json(cue) for cue in s.subtitle_cues]} for s in scheme.segments]}


def project_json(project: Project) -> dict[str, Any]:
    body_configs = {scheme_id: {row_key: body_config_json(config) for row_key, config in configs.items()} for scheme_id, configs in project.body_configs.items()}
    return {"project_id": project.project_id, "project_name": project.project_name, "excel_path": project.excel_path, "sheet_name": project.sheet_name, "hook_dir": project.hook_dir, "body_root_dir": project.body_root_dir, "output_dir": project.output_dir, "bgm_path": project.bgm_path, "bgm_volume": project.bgm_volume, "voice_volume": project.voice_volume, "hook_volume": project.hook_volume, "max_overlap": project.max_overlap, "recent_window": project.recent_window, "body_threshold": project.body_threshold, "output_limit": project.output_limit or 0, "output_format": project.output_format, "output_video_codec": project.output_video_codec, "output_resolution": project.output_resolution, "output_fps": project.output_fps, "output_video_bitrate": project.output_video_bitrate, "output_audio_bitrate": project.output_audio_bitrate, "output_audio_sample_rate": project.output_audio_sample_rate, "subtitle_style": style_json(project.subtitle_style), "errors": project.errors, "warnings": project.warnings, "body_mapping": project.body_mapping, "body_configs": body_configs, "schemes": [scheme_json(scheme) for scheme in project.schemes]}
