"""Retain installed Python distributions' upstream notices in the release."""
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / "build/dependency-licenses"
output.mkdir(parents=True, exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.is_file():
    raise RuntimeError("Python runtime LICENSE.txt is missing from the build environment")
shutil.copy2(python_license, output / "Python-LICENSE.txt")
packages = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata["Name"]
    packages.append({"name": name, "version": distribution.version,
                     "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
                     "homepage": distribution.metadata.get("Home-page")})
    for file in distribution.files or []:
        if any(part.lower().startswith(("license", "copying", "notice", "copyright")) for part in file.parts):
            source = Path(distribution.locate_file(file))
            if source.is_file():
                destination = output / name / Path(*[part for part in file.parts if part not in ("..", ".")])
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
(output / "packages.json").write_text(json.dumps(sorted(packages, key=lambda item: item["name"].lower()),
                                                ensure_ascii=False, indent=2), encoding="utf-8")
