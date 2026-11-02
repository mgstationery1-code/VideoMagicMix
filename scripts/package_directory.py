"""Export only the current runnable Windows folder and its per-file manifest."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    args = parser.parse_args()
    version = json.loads(Path('package.json').read_text(encoding='utf-8'))['version']
    folder = args.dist / 'win-unpacked'
    runtime = folder / 'resources/app-runtime'
    required = ['VideoMagicMix.exe', 'resources/app.asar']
    required += ['resources/app-runtime/' + p for p in (
        'videomagicmix-backend.exe', '_internal/python312.dll', 'tools/ffmpeg.exe', 'tools/ffprobe.exe',
        'fonts/SourceHanSansSC-Regular.otf', 'frontend/assets/fonts/HarmonyOS_Sans_SC_Regular.ttf',
        'frontend/assets/fonts/HarmonyOS_Sans_SC_Bold.ttf', 'frontend/assets/fonts/LICENSE.txt',
        'data/models/faster-whisper-small/model.bin', 'data/models/faster-whisper-small/config.json',
        'data/models/faster-whisper-small/tokenizer.json', 'data/models/faster-whisper-small/vocabulary.txt',
    )]
    for name in required:
        if not (folder / name).is_file():
            raise RuntimeError(f'Missing package file: {name}')
    for name in ('uploads', 'projects', 'cache', 'previews'):
        target = runtime / 'data' / name
        if target.exists() and any(target.rglob('*')):
            raise RuntimeError(f'User/runtime data must not be shipped: {target}')
    files = sorted(p for p in folder.rglob('*') if p.is_file() and p.name != 'FILE-MANIFEST.json')
    manifest = {'version': version, 'files': [
        {'path': p.relative_to(folder).as_posix(), 'size': p.stat().st_size, 'sha256': digest(p)} for p in files
    ]}
    manifest_path = folder / 'FILE-MANIFEST.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    output = args.dist / f'VideoMagicMix-Windows-{version}-x64.zip'
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in [*files, manifest_path]:
            archive.write(path, path.relative_to(folder).as_posix())
    assets = sorted(p for p in args.dist.iterdir() if p.is_file() and
                    (p.suffix in {'.exe', '.zip', '.blockmap'} or p.name == 'latest.yml'))
    (args.dist / 'SHA256SUMS.txt').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in assets), encoding='ascii')
    print(f'Exported {len(files)} application files: {output}', flush=True)


if __name__ == '__main__':
    main()
