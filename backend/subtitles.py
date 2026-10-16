from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from .models import SubtitleCue, SubtitleStyle


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT_EXTENSIONS = {".ttf", ".otf", ".ttc"}
FONT_STYLE_SUFFIXES = re.compile(r"\s+(bold|italic|regular|light|medium|semibold|black|oblique)$", re.IGNORECASE)
CHINESE_FONT_HINTS = (
    "yahei", "jhenghei", "simhei", "simsun", "fangsong", "kaiti", "noto", "source han",
    "cjk", "pingfang", "dengxian", "wenkai", "sarasa", "han yi", "hanyi", "chinese",
)


def _clean_font_name(value: str) -> str:
    name = re.sub(r"\s+\((?:TrueType|OpenType|Type 1)\)$", "", value, flags=re.IGNORECASE).strip()
    name = name.split("&", 1)[0].strip()
    return FONT_STYLE_SUFFIXES.sub("", name).strip() or name


def _font_record(name: str, source: str, path: str | None, supports_chinese: bool) -> dict[str, Any]:
    return {"name": name, "source": source, "path": path, "supports_chinese": supports_chinese}


def _system_font_records() -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    try:
        import winreg

        key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            values = [winreg.EnumValue(key, index) for index in range(winreg.QueryInfoKey(key)[1])]
        font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for raw_name, raw_path, _ in values:
            if not isinstance(raw_path, str):
                continue
            name = _clean_font_name(str(raw_name))
            path = Path(raw_path)
            if not path.is_absolute():
                path = font_dir / path
            if path.suffix.lower() not in FONT_EXTENSIONS or not path.is_file():
                continue
            key = name.casefold()
            supports = any(token in key for token in CHINESE_FONT_HINTS) or any(ord(char) > 127 for char in name) or bool(re.search(r"(?:^|[\s_-])sc(?:$|[\s_-])", key))
            records.setdefault(key, _font_record(name, "system", str(path), supports))
    except (OSError, ImportError):
        pass
    return list(records.values())


def _bundled_font_records(fonts_dir: Path) -> list[dict[str, Any]]:
    if not fonts_dir.is_dir():
        return []
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(fonts_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in FONT_EXTENSIONS:
            continue
        # ASS needs the font's internal family, not its distribution filename.
        name = "Source Han Sans SC" if path.name == "SourceHanSansSC-Regular.otf" else _clean_font_name(path.stem.replace("_", " "))
        records.setdefault(name.casefold(), _font_record(name, "bundled", str(path), True))
    return list(records.values())


def available_fonts(fonts_dir: str | Path | None = None) -> list[dict[str, Any]]:
    bundled = _bundled_font_records(Path(fonts_dir)) if fonts_dir else []
    records: dict[str, dict[str, Any]] = {item["name"].casefold(): item for item in _system_font_records()}
    for item in bundled:
        records[item["name"].casefold()] = item
    return sorted(records.values(), key=lambda item: (item["source"] != "bundled", item["name"].casefold()))


def public_font_records(fonts_dir: str | Path | None = None) -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items() if key != "path"} for item in available_fonts(fonts_dir)]


def default_font_name(fonts_dir: str | Path | None = None) -> tuple[str, str]:
    records = available_fonts(fonts_dir)
    names = {item["name"].casefold(): item for item in records}
    for candidate in ("microsoft yahei", "noto sans cjk sc", "noto sans sc", "source han sans sc", "source han sans cn", "simhei"):
        if candidate in names:
            return names[candidate]["name"], names[candidate]["source"]
    return (records[0]["name"], records[0]["source"]) if records else ("Arial", "system")


def _safe_color(value: str, fallback: str) -> str:
    return value.upper() if isinstance(value, str) and COLOR_RE.fullmatch(value) else fallback


def sanitize_style(style: SubtitleStyle, fonts_dir: str | Path | None = None) -> SubtitleStyle:
    records = available_fonts(fonts_dir)
    lookup = {item["name"].casefold(): item for item in records}
    font_name = str(style.font_name or "").strip()
    selected = lookup.get(font_name.casefold())
    if selected is None:
        font_name, source = default_font_name(fonts_dir)
    else:
        source = selected["source"]
    return SubtitleStyle(
        font_name=font_name,
        font_source=source,
        font_size=max(24, min(96, int(style.font_size))),
        bold=bool(style.bold),
        primary_color=_safe_color(style.primary_color, "#FFFFFF"),
        outline_color=_safe_color(style.outline_color, "#000000"),
        outline_width=max(0, min(8, int(style.outline_width))),
        shadow=max(0, min(6, int(style.shadow))),
        alignment=style.alignment if style.alignment in {2, 5, 8} else 2,
        margin_vertical=max(0, min(600, int(style.margin_vertical))),
        max_width=max(50, min(100, int(style.max_width))),
        max_chars_per_cue=max(1, min(50, int(style.max_chars_per_cue))),
    )


def style_json(style: SubtitleStyle) -> dict[str, Any]:
    return {
        "font_name": style.font_name,
        "font_source": style.font_source,
        "font_size": style.font_size,
        "bold": style.bold,
        "primary_color": style.primary_color,
        "outline_color": style.outline_color,
        "outline_width": style.outline_width,
        "shadow": style.shadow,
        "alignment": style.alignment,
        "margin_vertical": style.margin_vertical,
        "max_width": style.max_width,
        "max_chars_per_cue": style.max_chars_per_cue,
    }


CUE_SPLIT_RE = re.compile(r"[\r\n，,、；;。.!！?？]+")


def split_subtitle_text(text: str, max_chars: int = 10) -> list[str]:
    """Split on spoken punctuation first, then enforce the visible-char limit."""
    limit = max(1, min(50, int(max_chars)))
    cues: list[str] = []
    for phrase in CUE_SPLIT_RE.split(str(text or "")):
        phrase = "".join(phrase.split())
        if not phrase:
            continue
        cues.extend(phrase[index:index + limit] for index in range(0, len(phrase), limit))
    return cues


def cue_json(cue: SubtitleCue) -> dict[str, Any]:
    return {
        "row_key": cue.row_key,
        "text": cue.text,
        "start_seconds": cue.start_seconds,
        "end_seconds": cue.end_seconds,
        "confidence": cue.confidence,
        "matched": cue.matched,
    }


def _ass_color(value: str) -> str:
    color = value.lstrip("#")
    red, green, blue = color[0:2], color[2:4], color[4:6]
    return f"&H00{blue}{green}{red}"


def _ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total_cs, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _wrap_text(text: str, style: SubtitleStyle, play_res_x: int = 1080) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    max_chars = max(8, int(play_res_x * style.max_width / 100 / max(style.font_size * 0.95, 1)))
    lines: list[str] = []
    for source_line in text.split("\n"):
        while len(source_line) > max_chars:
            lines.append(source_line[:max_chars])
            source_line = source_line[max_chars:]
        lines.append(source_line)
    return r"\N".join(lines).replace("{", r"\{").replace("}", r"\}")


def _normalize_events(events: Iterable[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Make the rendered cue timeline strictly non-overlapping.

    Whisper timestamps can overlap by a few milliseconds at word boundaries.
    ASS renders overlapping Dialogue events simultaneously, so clamp each cue
    to the next cue's start and discard zero-length remnants.
    """
    ordered = sorted(
        ((max(0.0, float(start)), max(0.0, float(end)), str(text or "")) for start, end, text in events),
        key=lambda item: (item[0], item[1]),
    )
    normalized: list[tuple[float, float, str]] = []
    for index, (start, end, text) in enumerate(ordered):
        if not text:
            continue
        if normalized:
            previous_start, previous_end, previous_text = normalized[-1]
            if start < previous_end:
                normalized[-1] = (previous_start, min(previous_end, start), previous_text)
        if index + 1 < len(ordered):
            next_start = ordered[index + 1][0]
            end = min(end, next_start)
        if end <= start:
            continue
        normalized.append((start, end, text))
    return normalized


def build_ass(events: Iterable[tuple[float, float, str]], style: SubtitleStyle) -> str:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{style.font_name},{style.font_size},{_ass_color(style.primary_color)},{_ass_color(style.primary_color)},{_ass_color(style.outline_color)},&H80000000,{-1 if style.bold else 0},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},{style.alignment},40,40,{style.margin_vertical},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    # An explicit center anchor and fixed Y coordinate prevent ASS from moving
    # short cues when a font fallback or line-wrap decision changes.
    anchor_y = {8: style.margin_vertical, 5: 960, 2: 1920 - style.margin_vertical}.get(style.alignment, 1920 - style.margin_vertical)
    for start, end, text in _normalize_events(events):
        safe_text = _wrap_text(text, style).replace("\\N", " ")
        positioned = f"{{\\an{style.alignment}\\pos(540,{anchor_y})}}{safe_text}"
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{positioned}")
    return "\n".join(lines) + "\n"
