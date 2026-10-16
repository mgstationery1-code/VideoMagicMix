from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubtitleCue:
    row_key: str
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    confidence: float | None = None
    matched: bool = False


@dataclass
class ScriptSegment:
    row_key: str
    group_name: str
    text: str
    normalized_text: str
    enabled: bool = True
    skip_reason: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    subtitle_cues: list[SubtitleCue] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.start_seconds is None or self.end_seconds is None:
            return None
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass
class ScriptScheme:
    scheme_id: str
    source_column: str
    display_name: str
    segments: list[ScriptSegment] = field(default_factory=list)
    voice_path: str | None = None
    asr_status: str = "not_started"
    match_status: str = "not_started"
    transcript: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class BodyConfig:
    scheme_id: str
    row_key: str
    threshold: float = 1.15
    candidate_count: int = 1
    recommended_threshold: float | None = None
    actual_candidate_count: int = 0
    target_duration: float | None = None
    scanned_video_count: int = 0
    user_configured: bool = False
    control_mode: str = "candidate_count"


@dataclass
class SubtitleStyle:
    font_name: str = "Microsoft YaHei"
    font_source: str = "system"
    font_size: int = 52
    bold: bool = True
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 3
    shadow: int = 1
    alignment: int = 2
    margin_vertical: int = 120
    max_width: int = 90
    max_chars_per_cue: int = 10


@dataclass
class Project:
    project_id: str
    project_name: str
    excel_path: str
    sheet_name: str
    hook_dir: str = ""
    body_root_dir: str = ""
    output_dir: str = ""
    body_threshold: float = 1.15
    output_limit: int | None = None
    schemes: list[ScriptScheme] = field(default_factory=list)
    body_mapping: dict[str, str] = field(default_factory=dict)
    body_configs: dict[str, dict[str, BodyConfig]] = field(default_factory=dict)
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def scheme(self, scheme_id: str) -> ScriptScheme:
        for item in self.schemes:
            if item.scheme_id == scheme_id:
                return item
        raise KeyError(scheme_id)
