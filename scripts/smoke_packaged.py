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
    run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=green:s=320x568:r=25:d=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(body / "body.mp4")], env)
    project = request(base + "/api/projects/import-xlsx", files={"file": excel})
    assert not project["errors"], project
    endpoint = base + "/api/projects/" + project["project_id"]
    scheme_id = project["schemes"][0]["scheme_id"]
    request(endpoint + "/settings", {"hook_dir": hooks, "output_dir": output, "output_limit": "1"})
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
    check = request(endpoint + "/preflight", {})
    assert check["ok"] and check["planned_outputs"] == 1, check
    preview = request(endpoint + f"/subtitle-preview?scheme_id={scheme_id}&row_key=Body1")
    assert preview.startswith(b"\x89PNG")
    (work / "subtitle-preview.png").write_bytes(preview)
    result = request(endpoint + "/render", {}, timeout=240)
    assert result["ok"] and len(result["outputs"]) == 1, result
    media = json.loads(run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", result["outputs"][0]], env))
    assert any(stream["codec_type"] == "audio" for stream in media["streams"])
    assert any(stream.get("width") == 1080 and stream.get("height") == 1920 for stream in media["streams"])
    assert abs(float(media["format"]["duration"]) - 5) < 0.2
    (work / "result.json").write_text(json.dumps({"preflight": check, "render": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS Excel -> recursive Body -> upload -> frozen ASR -> preflight -> real ASS preview -> 1080x1920 MP4 (video + audio, one output)", flush=True)


if __name__ == "__main__":
    main()
