"""Export an allowlisted source ZIP without media, models or local runtimes."""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("backend", "frontend", "desktop", "tests", "scripts", "docs", ".github", "licenses")
FILES = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore", "package.json",
         "package-lock.json", "requirements.txt", "requirements-lock.txt", "requirements-build.txt", "run_backend.py", "fonts/LICENSES.md")
if __name__ == "__main__":
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    destination = ROOT / "dist" / f"VideoMagicMix-Source-{version}.zip"
    destination.parent.mkdir(exist_ok=True)
    paths = [ROOT / file for file in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".log"})
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            if path.is_file():
                archive.write(path, "VideoMagicMix/" + path.relative_to(ROOT).as_posix())
    print(destination)
