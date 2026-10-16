from __future__ import annotations

import os
import tempfile
import uuid
import math
import subprocess
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .asr import ASRError, FasterWhisperEngine, match_body_cues, transcript_from_json, transcript_json
from .excel_parser import import_workbook, preview_scheme
from .mixer import MediaInfo, Selection, _escape_filter_path, body_combination, body_combinations, body_threshold_for_count, complete_hook_candidates, list_videos, probe_duration, probe_media, render_video, resolve_binary
from .models import BodyConfig, Project, ScriptScheme, SubtitleStyle
from .planning import allocate_round_robin, normalize_output_limit
from .runtime import asr_worker_command, resource_root
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


def get_project(project_id: str) -> Project:
    try:
        return projects[project_id]
    except KeyError as exc:
        raise HTTPException(404, "项目不存在") from exc


def get_body_config(project: Project, scheme_id: str, row_key: str) -> BodyConfig:
    scheme_configs = project.body_configs.setdefault(scheme_id, {})
    return scheme_configs.setdefault(row_key, BodyConfig(scheme_id=scheme_id, row_key=row_key))


def body_config_json(config: BodyConfig) -> dict[str, Any]:
    return {
        "scheme_id": config.scheme_id,
        "row_key": config.row_key,
        "threshold": config.threshold,
        "candidate_count": config.candidate_count,
        "recommended_threshold": config.recommended_threshold,
        "actual_candidate_count": config.actual_candidate_count,
        "target_duration": config.target_duration,
        "scanned_video_count": config.scanned_video_count,
    }


def refresh_body_config(project: Project, scheme: ScriptScheme, segment, changed_by: str | None = None) -> tuple[BodyConfig, list[MediaInfo], list[Any]]:
    config = get_body_config(project, scheme.scheme_id, segment.row_key)
    folder = project.body_mapping.get(segment.row_key)
    target = segment.duration_seconds or 0
    if not folder or not target:
        config.target_duration = target or None
        config.scanned_video_count = 0
        config.actual_candidate_count = 0
        config.recommended_threshold = None
        return config, [], []
    paths = list_videos(folder)
    infos = [MediaInfo(path, probe_duration(path)) for path in paths]
    config.target_duration = target
    config.scanned_video_count = len(paths)
    desired = max(1, int(config.candidate_count or 1))
    recommended = body_threshold_for_count(infos, target, desired)
    config.recommended_threshold = recommended
    if (changed_by == "candidate_count" or (changed_by is None and config.control_mode == "candidate_count")) and recommended is not None:
        config.threshold = recommended
    elif not config.user_configured and config.target_duration and config.recommended_threshold is not None:
        config.threshold = recommended
    combinations = body_combinations(infos, target, config.threshold)
    config.actual_candidate_count = len(combinations)
    if changed_by == "threshold":
        config.candidate_count = config.actual_candidate_count
        config.recommended_threshold = body_threshold_for_count(infos, target, max(1, config.candidate_count)) if config.candidate_count else None
    return config, infos, combinations


def _body_segments(scheme: ScriptScheme) -> list[Any]:
    return [segment for segment in scheme.segments if segment.enabled and segment.row_key.lower() != "hook"]


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
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
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
def settings(project_id: str, hook_dir: str = Form(""), body_root_dir: str = Form(""), output_dir: str = Form(""), body_threshold: float = Form(1.15), output_limit: str = Form("")) -> dict[str, Any]:
    project = get_project(project_id)
    try:
        normalized_limit = normalize_output_limit(output_limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    project.hook_dir, project.body_root_dir, project.output_dir = hook_dir.strip(), body_root_dir.strip(), output_dir.strip()
    project.output_limit = normalized_limit
    return project_json(project)


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
    if value:
        project.body_mapping[key] = value
    else:
        project.body_mapping.pop(key, None)
    return project_json(project)


@app.post("/api/projects/{project_id}/body-config")
def update_body_config(project_id: str, scheme_id: str = Form(...), row_key: str = Form(...), threshold: str = Form(""), candidate_count: str = Form(""), changed_by: str = Form("threshold")) -> dict[str, Any]:
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
        if value < 1:
            raise HTTPException(400, "Body 阈值必须大于或等于 1.0")
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


@app.post("/api/projects/{project_id}/schemes/{scheme_id}/voice")
async def upload_voice(project_id: str, scheme_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    project = get_project(project_id)
    scheme = project.scheme(scheme_id)
    suffix = Path(file.filename or "voice.bin").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
        raise HTTPException(400, "配音格式必须是 mp3/wav/m4a/aac/flac/ogg/opus")
    target = UPLOADS / f"{project_id}_{scheme_id}{suffix}"
    target.write_bytes(await file.read())
    for task_id, task in transcription_tasks.items():
        if task.get("project_id") == project_id and task.get("scheme_id") == scheme_id and task.get("status") in {"queued", "running"}:
            process = active_asr_processes.get(task_id)
            if process and process.poll() is None:
                process.kill()
            task.update(status="failed", message="已由新上传的配音替换", error="识别任务已取消")
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
    hook_info: list[MediaInfo] = []
    for path in list_videos(project.hook_dir):
        try:
            info = probe_media(path)
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
    if hook_info:
        plan.append({"row_key": "Hook", "hook_count": len(hook_info), "candidate_count": len(hook_info), "options": [item.path for item in hook_info], "message": f"已扫描 {len(hook_info)} 个 Hook，全部作为完整候选"})
        for scheme in schemes_to_check:
          candidate_counts: list[int] = [len(hook_info)]
          for segment in _body_segments(scheme):
            target = segment.duration_seconds or 0
            folder = project.body_mapping.get(segment.row_key)
            if not folder:
                failures.append(f"{scheme.display_name}/{segment.row_key} 未匹配 Body 目录：{segment.group_name}")
                candidate_counts.append(0)
                continue
            config, infos, combinations = refresh_body_config(project, scheme, segment)
            if not combinations:
                selection = body_combination(infos, target, config.threshold)
                failures.append(f"{scheme.display_name}/{segment.row_key}：{selection.message}")
            candidate_counts.append(len(combinations))
            plan.append({"scheme_id": scheme.scheme_id, "display_name": scheme.display_name, "row_key": segment.row_key, "target": target, "target_duration": target, "scanned_video_count": config.scanned_video_count, "candidate_count": config.candidate_count, "actual_candidate_count": len(combinations), "threshold": config.threshold, "recommended_threshold": config.recommended_threshold, "files": [[f.path for f in selection.files] for selection in combinations[:5]], "speed_factor": combinations[0].speed_factor if combinations else None, "subtitle_cues": [cue_json(cue) for cue in segment.subtitle_cues]})
          estimate = math.prod(candidate_counts) if candidate_counts and all(candidate_counts) else 0
          body_duration = sum(segment.duration_seconds or 0 for segment in _body_segments(scheme))
          scheme_estimates.append({"scheme_id": scheme.scheme_id, "display_name": scheme.display_name, "estimated_outputs": estimate, "candidate_counts": candidate_counts, "hook_count": len(hook_info), "body_voice_duration": body_duration})
    total_estimate = sum(item["estimated_outputs"] for item in scheme_estimates)
    allocations = allocate_round_robin(project.output_limit, [item["estimated_outputs"] for item in scheme_estimates])
    for item, planned in zip(scheme_estimates, allocations):
        item["planned_outputs"] = planned
    planned_outputs = sum(allocations)
    return {
        "ok": not failures,
        "failures": failures,
        "plan": plan,
        "body_configs": {scheme_id: {row_key: body_config_json(config) for row_key, config in configs.items()} for scheme_id, configs in project.body_configs.items()},
        "estimated_outputs": total_estimate,
        "output_limit": project.output_limit or 0,
        "planned_outputs": planned_outputs,
        "hook_count": len(hook_info),
        "scheme_estimates": scheme_estimates,
    }


@app.post("/api/projects/{project_id}/render")
def render(project_id: str, scheme_id: str = Form(""), limit: str = Form("")) -> dict[str, Any]:
    project = get_project(project_id)
    if scheme_id:
        schemes = [project.scheme(scheme_id)]
    else:
        schemes = [scheme for scheme in project.schemes if scheme.voice_path]
    if not schemes:
        raise HTTPException(400, "没有可渲染的字幕方案")
    try:
        request_limit = normalize_output_limit(limit) if limit and limit.strip() else project.output_limit
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    outputs: list[str] = []
    check = preflight(project_id, scheme_id or None)
    if not check["ok"]:
        raise HTTPException(400, {"message": "预检未通过", "failures": check["failures"], "estimated_outputs": check.get("estimated_outputs", 0), "planned_outputs": check.get("planned_outputs", 0)})
    if request_limit is None:
        output_budget = check["estimated_outputs"]
    else:
        output_budget = min(request_limit, check["estimated_outputs"])
    if output_budget <= 0:
        raise HTTPException(400, "当前没有可生成的组合")
    output_dir = Path(project.output_dir or (Path(project.excel_path).parent / "VideoMagicMix-输出"))
    output_dir.mkdir(parents=True, exist_ok=True)
    hook_infos = [probe_media(path) for path in list_videos(project.hook_dir)]
    streams: list[tuple[ScriptScheme, Selection, Any]] = []
    for scheme in schemes:
        body_sets: list[list[Any]] = []
        for segment in _body_segments(scheme):
            _, _, combinations = refresh_body_config(project, scheme, segment)
            body_sets.append(combinations)
        for hook in complete_hook_candidates(hook_infos):
            streams.append((scheme, hook, iter(product(*body_sets)) if body_sets else iter([tuple()])))

    active = list(range(len(streams)))
    while active and len(outputs) < output_budget:
        for index in list(active):
            if len(outputs) >= output_budget:
                break
            scheme, hook, combinations = streams[index]
            try:
                body_combination_set = next(combinations)
            except StopIteration:
                active.remove(index)
                continue
            selections = [hook, *list(body_combination_set)]
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
            output_path = output_dir / f"{project.project_name}-{scheme.display_name}-{output_number:03d}.mp4"
            try:
                render_video(selections, scheme.voice_path, str(output_path), subtitle_path=str(ass_path), subtitle_fonts_dir=str(BUNDLED_FONTS) if BUNDLED_FONTS.is_dir() else None)
            except Exception as exc:
                generated = len(outputs)
                raise HTTPException(500, {"message": str(exc), "failed_index": output_number, "generated_count": generated, "remaining_count": max(0, output_budget - generated), "outputs": outputs}) from exc
            finally:
                ass_path.unlink(missing_ok=True)
            outputs.append(str(output_path))
    return {"ok": True, "scheme_id": scheme_id or None, "outputs": outputs, "output": outputs[0] if outputs else None, "count": len(outputs), "estimated_outputs": check["estimated_outputs"], "planned_outputs": len(outputs), "output_limit": request_limit or 0}


def scheme_json(scheme) -> dict[str, Any]:
    return {"scheme_id": scheme.scheme_id, "source_column": scheme.source_column, "display_name": scheme.display_name, "voice_path": scheme.voice_path, "asr_status": scheme.asr_status, "match_status": scheme.match_status, "error": scheme.error, "preview": preview_scheme(scheme), "segments": [{"row_key": s.row_key, "group_name": s.group_name, "text": s.text, "start": s.start_seconds, "end": s.end_seconds, "duration": s.duration_seconds, "enabled": s.enabled, "skip_reason": s.skip_reason, "subtitle_cues": [cue_json(cue) for cue in s.subtitle_cues]} for s in scheme.segments]}


def project_json(project: Project) -> dict[str, Any]:
    body_configs = {scheme_id: {row_key: body_config_json(config) for row_key, config in configs.items()} for scheme_id, configs in project.body_configs.items()}
    return {"project_id": project.project_id, "project_name": project.project_name, "excel_path": project.excel_path, "sheet_name": project.sheet_name, "hook_dir": project.hook_dir, "body_root_dir": project.body_root_dir, "output_dir": project.output_dir, "body_threshold": project.body_threshold, "output_limit": project.output_limit or 0, "subtitle_style": style_json(project.subtitle_style), "errors": project.errors, "warnings": project.warnings, "body_mapping": project.body_mapping, "body_configs": body_configs, "schemes": [scheme_json(scheme) for scheme in project.schemes]}
