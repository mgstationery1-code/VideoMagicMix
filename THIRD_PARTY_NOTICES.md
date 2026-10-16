# Third-party components

VideoMagicMix application source is MIT licensed. Bundled components retain
their own licenses; the application license does not replace them.

| Component | Source | License / included notices |
| --- | --- | --- |
| VideoMatrix reference workflow | https://github.com/w2968066/VideoMatrix-- | MIT; `licenses/VideoMatrix-MIT.txt` |
| Electron / Chromium / Node.js | https://github.com/electron/electron | Electron MIT and Chromium notices in the desktop distribution |
| Python | https://www.python.org/ | PSF; standalone runtime includes Python notices |
| faster-whisper / CTranslate2 | https://github.com/SYSTRAN/faster-whisper / https://github.com/OpenNMT/CTranslate2 | MIT; collected installed-distribution licenses |
| Whisper small weights | https://huggingface.co/Systran/faster-whisper-small | OpenAI Whisper MIT; `licenses/Whisper-MIT.txt` |
| Source Han Sans SC Regular | https://github.com/adobe-fonts/source-han-sans | SIL OFL 1.1; `fonts/SourceHanSans-LICENSE.txt` |
| FFmpeg / FFprobe 8.1.1 essentials | https://github.com/GyanD/codexffmpeg/releases/tag/8.1.1 | GPL-enabled build; archive notices in `tools/ffmpeg-licenses/` |

Python package versions and available upstream license files are collected into
`build/dependency-licenses/` and included as `resources/app-runtime/licenses/python/`.

FFmpeg archive URL and SHA256 are pinned in `scripts/prepare_assets.py`.
Extracted binary hashes are retained in `tools/ASSET-RECORD.json` in the release.
Upstream FFmpeg source: https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz.
Build scripts and dependency-source instructions: https://github.com/GyanD/codexffmpeg
and https://www.gyan.dev/ffmpeg/builds/ (also see the included build README).
Distributors must provide GPL corresponding-source access for the actual build
and its enabled libraries; the main FFmpeg source archive alone is not a complete
source bundle. Retain all notices and source instructions when publishing.

Windows system fonts are discovered locally and are never redistributed.
User spreadsheets, narration, source videos and outputs are excluded from releases.
