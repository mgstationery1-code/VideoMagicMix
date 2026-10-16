"""Fetch redistributable release assets at build time, never at app startup."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build-cache"
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_FILES = {
    "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
    "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}
FONT_REVISION = "a4f7cf94edfb9d7ffbdfc4841de276358bd7e0f2"
FFMPEG_URL = "https://github.com/GyanD/codexffmpeg/releases/download/8.1.1/ffmpeg-8.1.1-essentials_build.zip"
FFMPEG_SHA256 = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url: str, target: Path, expected: str | None = None, *, headers: tuple[str, ...] = (), label: str | None = None) -> None:
    if target.is_file() and (expected is None or sha256(target) == expected):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    print(f"Downloading {label or url}", flush=True)
    try:
        # Windows and the GitHub Windows runner include curl. Bound connection,
        # stalled transfers and total time so a build cannot wait indefinitely.
        command = ["curl.exe", "--fail", "--location", "--silent", "--show-error",
                   "--connect-timeout", "15", "--max-time", "600", "--speed-time", "45",
                   "--speed-limit", "1024", "--retry", "1", "--user-agent", "VideoMagicMix-build/0.1"]
        for header in headers:
            command.extend(["--header", header])
        subprocess.run([*command, "--output", str(temporary), url], check=True, timeout=1250)
        if expected and sha256(temporary) != expected:
            raise RuntimeError(f"SHA256 mismatch: {target.name}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def download_ffmpeg(archive: Path) -> None:
    """Resume bounded range requests on networks that stall large downloads."""
    if archive.is_file() and sha256(archive) == FFMPEG_SHA256:
        return
    # Hosted runners have fast release downloads, but shared anonymous API quotas.
    if os.getenv("GITHUB_ACTIONS") == "true":
        download(FFMPEG_URL, archive, FFMPEG_SHA256)
        return
    endpoint = "https://api.github.com/repos/GyanD/codexffmpeg/releases/assets/411752976"
    response = subprocess.check_output(
        ["curl.exe", "--fail", "--silent", "--show-error", "--head",
         "--connect-timeout", "15", "--max-time", "30", "--header",
         "Accept: application/octet-stream", endpoint], timeout=35).decode("utf-8")
    location = next((line.split(":", 1)[1].strip() for line in response.splitlines()
                     if line.lower().startswith("location:")), None)
    if not location:
        download(FFMPEG_URL, archive, FFMPEG_SHA256)
        return
    total, chunk_size = 109282242, 2 * 1024 * 1024
    parts = CACHE / "ffmpeg-parts"
    parts.mkdir(parents=True, exist_ok=True)

    def fetch(start: int) -> Path:
        end = min(total - 1, start + chunk_size - 1)
        part = parts / f"{start:012d}.part"
        if part.is_file() and part.stat().st_size != end - start + 1:
            part.unlink()
        download(location, part, headers=(f"Range: bytes={start}-{end}",), label=f"FFmpeg bytes {start}-{end}")
        if part.stat().st_size != end - start + 1:
            raise RuntimeError(f"Invalid FFmpeg range length: {start}")
        return part

    temporary = archive.with_name(archive.name + ".partial")
    with ThreadPoolExecutor(max_workers=8) as pool:
        downloaded = list(pool.map(fetch, range(0, total, chunk_size)))
    with temporary.open("wb") as output:
        for part in downloaded:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output)
    if sha256(temporary) != FFMPEG_SHA256:
        raise RuntimeError("FFmpeg archive SHA256 mismatch")
    temporary.replace(archive)


def prepare_models() -> None:
    for name, digest in MODEL_FILES.items():
        download(f"https://huggingface.co/Systran/faster-whisper-small/resolve/{MODEL_REVISION}/{name}",
                 ROOT / "data/models/faster-whisper-small" / name, digest)


def prepare_font() -> None:
    font = ROOT / "fonts/SourceHanSansSC-Regular.otf"
    if not font.is_file():
        download(f"https://raw.githubusercontent.com/adobe-fonts/source-han-sans/{FONT_REVISION}/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf",
                 font)
    content = font.read_bytes()
    digest = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
    if digest != "8113ad53da18b6a08b133c6c460ae1d818a0f865":
        raise RuntimeError("Font Git object verification failed")
    license_path = ROOT / "fonts/SourceHanSans-LICENSE.txt"
    if not license_path.is_file():
        download_github_text("adobe-fonts/source-han-sans", "LICENSE.txt", FONT_REVISION, license_path)


def download_github_text(repository: str, name: str, revision: str, target: Path) -> None:
    if target.is_file():
        return
    download(f"https://raw.githubusercontent.com/{repository}/{revision}/{name}", target)


def prepare_media_tools() -> None:
    record_path = ROOT / "tools/ASSET-RECORD.json"
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("archive_sha256") == FFMPEG_SHA256 and all(
            (ROOT / "tools" / name).is_file() and sha256(ROOT / "tools" / name) == digest
            for name, digest in record.get("binaries", {}).items()
        ) and set(record.get("binaries", {})) == {"ffmpeg.exe", "ffprobe.exe"}:
            return
    archive = CACHE / "ffmpeg-8.1.1-essentials_build.zip"
    download_ffmpeg(archive)
    hashes = {}
    with zipfile.ZipFile(archive) as bundle:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            member = next(item for item in bundle.namelist() if item.endswith("/bin/" + name))
            destination = ROOT / "tools" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Preserve the user's previous local tool on the first replacement.
            if destination.exists():
                backup = CACHE / "previous-tools" / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    shutil.copy2(destination, backup)
            with bundle.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            hashes[name] = sha256(destination)
        notices = ROOT / "tools/ffmpeg-licenses"
        notices.mkdir(parents=True, exist_ok=True)
        for member in bundle.namelist():
            name = Path(member).name
            if name.lower().startswith(("license", "readme")) and not member.endswith("/"):
                (notices / name).write_bytes(bundle.read(member))
    record_path.write_text(json.dumps({"url": FFMPEG_URL, "archive_sha256": FFMPEG_SHA256,
                                       "binaries": hashes}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    prepare_models()
    prepare_font()
    prepare_media_tools()
    download_github_text("w2968066/VideoMatrix--", "LICENSE", "main", ROOT / "licenses/VideoMatrix-MIT.txt")
    download_github_text("openai/whisper", "LICENSE", "main", ROOT / "licenses/Whisper-MIT.txt")
    print("Release assets ready (model, font, FFmpeg, FFprobe).", flush=True)
