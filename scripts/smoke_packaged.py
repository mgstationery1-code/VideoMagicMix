"""Exercise only the frozen backend with PATH empty and model downloads disabled."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def request(url: str, fields: dict | None = None, files: dict | None = None, timeout: int = 30):
    headers = {}
    data = None
    if fields is not None or files:
        boundary = uuid.uuid4().hex
        pieces = []
        for name, value in (fields or {}).items():
            pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for name, path in (files or {}).items():
            pieces.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(), path.read_bytes(), b"\r\n"])
        pieces.append(f"--{boundary}--\r\n".encode())
        data = b"".join(pieces)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as response:
            payload = response.read()
            return json.loads(payload) if "application/json" in response.headers.get("Content-Type", "") else payload
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode("utf-8", errors="replace")) from error


def run(command: list[str], env: dict, timeout: int = 120) -> str:
    result = subprocess.run(command, env=env, capture_output=True, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-5000:])
    return result.stdout.decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--voice", type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    root = args.runtime.resolve()
    executable = root / "videomagicmix-backend.exe"
    assert executable.is_file(), executable
    assert not (root / ".venv").exists(), "A release must not depend on a copied venv"
    for relative in ("_internal/python312.dll", "frontend/index.html", "tools/ffmpeg.exe", "tools/ffprobe.exe", "fonts/SourceHanSansSC-Regular.otf", "data/models/faster-whisper-small/model.bin"):
        assert (root / relative).is_file(), f"Missing release resource: {relative}"
    work = Path(tempfile.mkdtemp(prefix="VideoMagicMix-发布测试 "))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = uuid.uuid4().hex
    env = {**os.environ, "PATH": "", "VMM_RESOURCE_DIR": str(root), "VMM_DATA_DIR": str(work / "data"),
           "VMM_PORT": str(port), "VMM_INSTANCE_TOKEN": token, "HF_HUB_OFFLINE": "1",
           "WHISPER_MODEL_PATH": str(root / "data/models/faster-whisper-small"),
           "WHISPER_DEVICE": "cpu", "WHISPER_COMPUTE_TYPE": "int8"}
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    ffmpeg = str(root / "tools/ffmpeg.exe")
    ffprobe = str(root / "tools/ffprobe.exe")
    run([ffmpeg, "-version"], env)
    run([ffprobe, "-version"], env)
    base = f"http://127.0.0.1:{port}"
    with (work / "backend.log").open("wb") as log:
        process = subprocess.Popen([str(executable)], cwd=work, env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    raise RuntimeError((work / "backend.log").read_text(encoding="utf-8", errors="replace"))
                try:
                    health = request(base + "/api/health")
                    assert health["instance_token"] == token
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Frozen backend startup timed out")
                    time.sleep(0.25)
            assert b"VideoMagicMix" in request(base + "/")
            assert request(base + "/static/app.js") == (root / "frontend/app.js").read_bytes()
            assert request(base + "/api/asr/health")["status"] == "ready"
            assert request(base + "/api/fonts")["count"] > 0
            print("PASS frozen health, UI, fonts, model discovery, FFmpeg/FFprobe; PATH is empty", flush=True)
            if args.voice:
                voice = work / "voice.wav"
                run([ffmpeg, "-y", "-i", str(args.voice.resolve()), "-t", "4", "-ar", "16000", "-ac", "1", str(voice)], env)
                transcript_file = work / "transcript.json"
                run([str(executable), "--asr-worker", str(voice), str(transcript_file)], env, 240)
                transcript = json.loads(transcript_file.read_text(encoding="utf-8"))
                assert transcript["ok"] and transcript["transcript"]
                assert any(segment["words"] for segment in transcript["transcript"])
                print("PASS offline frozen ASR worker with word timestamps", flush=True)
                if args.render:
                    exercise_render(base, work, voice, transcript, ffmpeg, ffprobe, env)
            elif args.render:
                raise ValueError("--render requires --voice")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    print(f"SMOKE_OK artifacts: {work}", flush=True)


def exercise_render(base: str, work: Path, voice: Path, transcript: dict, ffmpeg: str, ffprobe: str, env: dict) -> None:
    from openpyxl import Workbook
    import wave
    import numpy as np

    def assert_hook_without_bgm(path: Path) -> None:
        with wave.open(str(path), "rb") as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
            samples = samples.reshape(-1, audio.getnchannels()).mean(axis=1)
            sample_rate = audio.getframerate()
        samples = samples[int(.2 * sample_rate):int(.7 * sample_rate)]
        times = np.arange(len(samples)) / sample_rate
        hook_tone, music_tone = [abs(np.sum(samples * np.exp(-2j * np.pi * hz * times))) for hz in [440, 700]]
        assert hook_tone > 0 and music_tone < hook_tone * .01, (hook_tone, music_tone)
    text = "".join(segment["text"] for segment in transcript["transcript"])
    workbook = Workbook()
    workbook.active.append(["段落", "分组", "字幕1"])
    workbook.active.append(["Hook", "完整开头", ""])
    workbook.active.append(["Body1", "测试画面", text])
    excel = work / "script.xlsx"
    workbook.save(excel)
    hooks, body, output = work / "hooks", work / "body/subfolder", work / "output"
    hooks.mkdir()
    body.mkdir(parents=True)
    output.mkdir()
    run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x568:r=25:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(hooks / "hook.mp4")], env)
    for name, duration, color in [("slow", 3.6, "green"), ("normal", 4, "red"), ("fast", 5.2, "yellow")]:
        run([ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x568:r=25:d={duration}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(body / f"{name}.mp4")], env)
    music = work / "music.wav"
    run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=700:duration=0.5", str(music)], env)
    project = request(base + "/api/projects/import-xlsx", files={"file": excel})
    assert not project["errors"], project
    endpoint = base + "/api/projects/" + project["project_id"]
    scheme_id = project["schemes"][0]["scheme_id"]
    request(endpoint + "/settings", {"hook_dir": hooks, "output_dir": output, "output_limit": "1",
                                     "voice_volume": "3", "hook_volume": "2", "bgm_volume": "0.5", "max_overlap": "30"})
    request(endpoint + "/bgm", files={"file": music})
    request(endpoint + "/subtitle-style", {"font_name": "Source Han Sans SC"})
    request(endpoint + "/mapping/body", {"group_key": "Body1", "directory": body.parent})
    request(endpoint + f"/schemes/{scheme_id}/voice", files={"file": voice})
    task = request(endpoint + f"/schemes/{scheme_id}/transcribe", {})
    deadline = time.monotonic() + 240
    while task["status"] in {"queued", "running"}:
        if time.monotonic() > deadline:
            raise RuntimeError("HTTP ASR task timed out")
        time.sleep(1)
        task = request(base + "/api/tasks/" + task["task_id"])
    assert task["status"] == "completed", task
    combinations_endpoint = endpoint + f"/schemes/{scheme_id}/bodies/Body1"
    listing = request(combinations_endpoint + "/combinations")
    assert listing["candidate_count"] == 3 and listing["selected_count"] == 3, listing
    assert all(item["preview_url"] is None for item in listing["combinations"]), listing
    excluded = listing["combinations"][0]
    updated = request(combinations_endpoint + "/selection", {"identity": excluded["id"], "selected": "false"})
    assert updated["config"]["actual_candidate_count"] == 2, updated
    preview_task = request(combinations_endpoint + "/pre-render", {})
    deadline = time.monotonic() + 120
    while preview_task["status"] in {"queued", "running"}:
        if time.monotonic() > deadline:
            raise RuntimeError("Frozen Body pre-render task timed out")
        time.sleep(0.5)
        preview_task = request(base + "/api/preview-tasks/" + preview_task["task_id"])
    assert preview_task["status"] == "completed" and preview_task["completed"] == 2, preview_task
    listing = request(combinations_endpoint + "/combinations")
    assert all(bool(item["preview_url"]) == item["selected"] for item in listing["combinations"]), listing
    for item in listing["combinations"]:
        if not item["selected"]:
            continue
        clip = work / (item["id"] + ".mp4")
        clip.write_bytes(request(base + item["preview_url"]))
        media = json.loads(run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(clip)], env))
        assert abs(float(media["format"]["duration"]) - 4) < 0.1, media
        assert not any(stream["codec_type"] == "audio" for stream in media["streams"]), media
    for row, duration in [("Hook", 1), ("Body1", 4)]:
        audition = work / f"audition-{row}.wav"
        audition.write_bytes(request(endpoint + "/audio-preview", {"scheme_id": scheme_id, "row_key": row}))
        media = json.loads(run([ffprobe, "-v", "error", "-show_format", "-of", "json", str(audition)], env))
        assert abs(float(media["format"]["duration"]) - duration) < 0.1, media
        if row == "Hook":
            assert_hook_without_bgm(audition)
    print("PASS frozen default speed range, 3 candidates, deselection, checked-only previews, Body mixed audition and Hook audition without BGM", flush=True)
    check = request(endpoint + "/preflight", {})
    assert check["ok"] and check["planned_outputs"] == 1, check
    assert check["overlap"]["strategy"] == "balanced_rotation", check
    assert check["overlap"]["recent_window"] == 3, check
    assert check["overlap"]["preview_jobs"][0]["bodies"][0]["row_key"] == "Body1", check
    preview = request(endpoint + f"/subtitle-preview?scheme_id={scheme_id}&row_key=Body1")
    assert preview.startswith(b"\x89PNG")
    (work / "subtitle-preview.png").write_bytes(preview)
    assert check["plan_id"] and check["overlap"]["max_pair_overlap"] is None, check
    result = request(endpoint + "/render", {"plan_id": check["plan_id"]}, timeout=240)
    assert result["plan_id"] == check["plan_id"], result
    assert result["ok"] and len(result["outputs"]) == 1, result
    media = json.loads(run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", result["outputs"][0]], env))
    assert any(stream["codec_type"] == "audio" for stream in media["streams"])
    assert any(stream.get("width") == 1080 and stream.get("height") == 1920 for stream in media["streams"])
    assert abs(float(media["format"]["duration"]) - 5) < 0.2
    hook_audio = work / "rendered-hook.wav"
    run([ffmpeg, "-y", "-i", result["outputs"][0], "-t", "1", "-vn", "-acodec", "pcm_s16le", str(hook_audio)], env)
    assert_hook_without_bgm(hook_audio)
    (work / "result.json").write_text(json.dumps({"preflight": check, "render": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    run([ffmpeg, "-v", "error", "-i", result["outputs"][0], "-f", "null", "-"], env)
    print("PASS Excel -> recursive Body -> upload -> frozen ASR -> balanced rotation preflight -> ASS preview -> 1080x1920 video; Hook has no BGM, Body music loops; complete video decodes", flush=True)

    # Verify the frozen server also enforces acknowledgement, not just the UI.
    request(endpoint + '/settings', {'hook_dir': hooks, 'output_dir': output, 'output_limit': '3',
                                    'recent_window': '1', 'max_overlap': '50',
                                    'output_format': 'mp4', 'output_video_codec': 'h264', 'output_video_bitrate': '8M'})
    batch = request(endpoint + '/preflight', {})
    assert batch['planned_outputs'] == 2 and batch['requires_acceptance'], batch
    try:
        request(endpoint + '/render', {'plan_id': batch['plan_id']})
        raise AssertionError('Frozen server rendered without explicit acceptance')
    except RuntimeError as exc:
        assert isinstance(exc.__cause__, urllib.error.HTTPError) and exc.__cause__.code == 409, str(exc)
    rendered = request(endpoint + '/render', {'plan_id': batch['plan_id'], 'accept_warnings': 'true'}, timeout=240)
    assert rendered['count'] == 2 and rendered['overlap']['pair_count'] == 1, rendered
    assert rendered['overlap']['max_pair_overlap'] == 0, rendered
    for output in rendered['outputs']:
        run([ffmpeg, '-v', 'error', '-i', output, '-f', 'null', '-'], env)
    print('PASS frozen 2-output rotation batch, explicit shortage acceptance and full decode', flush=True)


if __name__ == "__main__":
    main()
