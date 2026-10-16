from __future__ import annotations

import os
import re
import math
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from .excel_parser import normalize_text
from .models import SubtitleCue
from .subtitles import split_subtitle_text
from .runtime import resource_root


@dataclass
class TranscriptWord:
    text: str
    start: float
    end: float
    confidence: float | None = None


@dataclass
class TranscriptSegment:
    text: str
    start: float
    end: float
    confidence: float | None = None
    words: list[TranscriptWord] | None = None


class ASRError(RuntimeError):
    pass


class ASREngine:
    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        raise NotImplementedError


class FasterWhisperEngine(ASREngine):
    def __init__(self, model_size: str | None = None, device: str | None = None):
        self.model_size = model_size or os.getenv("WHISPER_MODEL", "small")
        # CPU int8 is the reliable default for the packaged Windows app.  Users
        # can still opt into CUDA explicitly with WHISPER_DEVICE=cuda.
        self.device = device or os.getenv("WHISPER_DEVICE", "cpu")
        self.model_path = os.getenv("WHISPER_MODEL_PATH", "").strip()
        cache_dir = Path(os.getenv("WHISPER_CACHE_DIR") or resource_root() / "data" / "models")
        local_model = cache_dir / f"faster-whisper-{self.model_size}"
        if not self.model_path and (local_model / "model.bin").is_file():
            self.model_path = str(local_model)
        self._model = None
        self._active_device: str | None = None

    def _load(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ASRError("未安装 faster-whisper。请重新安装 VideoMagicMix 的本地依赖。") from exc
        if self._model is None:
            compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
            cache_dir = os.getenv("WHISPER_CACHE_DIR") or str(resource_root() / "data" / "models")
            os.environ.setdefault("HF_HOME", cache_dir)
            model_source = self.model_path or self.model_size
            if getattr(sys, "frozen", False) and not self.model_path:
                raise ASRError("安装包缺少本地 Whisper 模型，请重新下载完整安装包；桌面版不会自动联网下载模型。")
            preferred_device = "cuda" if self.device == "auto" and ctranslate2_cuda_available() else ("cpu" if self.device == "auto" else self.device)
            try:
                self._model = WhisperModel(model_source, device=preferred_device, compute_type=compute_type)
                self._active_device = preferred_device
            except Exception:
                if preferred_device != "cuda":
                    raise
                self._model = WhisperModel(model_source, device="cpu", compute_type="int8")
                self._active_device = "cpu"
        return self._model

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        if not os.path.isfile(audio_path):
            raise ASRError(f"配音文件不存在：{audio_path}")
        model = self._load()
        try:
            segments, _ = model.transcribe(audio_path, language="zh", vad_filter=True, word_timestamps=True)
            return [_whisper_segment(s) for s in segments]
        except Exception:
            if self._active_device != "cuda":
                raise
            from faster_whisper import WhisperModel
            compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
            self._model = WhisperModel(self.model_path or self.model_size, device="cpu", compute_type="int8" if compute_type == "int8" else compute_type)
            self._active_device = "cpu"
            segments, _ = self._model.transcribe(audio_path, language="zh", vad_filter=True, word_timestamps=True)
            return [_whisper_segment(s) for s in segments]


def _whisper_segment(segment: Any) -> TranscriptSegment:
    words = [
        TranscriptWord(
            text=str(word.word).strip(),
            start=float(word.start),
            end=float(word.end),
            confidence=float(word.probability) if word.probability is not None else None,
        )
        for word in (segment.words or [])
        if word.start is not None and word.end is not None and str(word.word).strip()
    ]
    return TranscriptSegment(text=segment.text.strip(), start=float(segment.start), end=float(segment.end), confidence=None, words=words)


def ctranslate2_cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def get_asr_engine() -> ASREngine:
    return FasterWhisperEngine()


def transcript_json(items: list[TranscriptSegment]) -> list[dict[str, Any]]:
    return [{
        "text": item.text,
        "start": item.start,
        "end": item.end,
        "confidence": item.confidence,
        "words": [{"text": word.text, "start": word.start, "end": word.end, "confidence": word.confidence} for word in (item.words or [])],
    } for item in items]


def transcript_from_json(items: list[dict[str, Any]]) -> list[TranscriptSegment]:
    return [TranscriptSegment(
        text=item.get("text", ""),
        start=float(item.get("start", 0)),
        end=float(item.get("end", 0)),
        confidence=item.get("confidence"),
        words=[TranscriptWord(
            text=word.get("text", ""), start=float(word.get("start", 0)), end=float(word.get("end", 0)), confidence=word.get("confidence")
        ) for word in item.get("words", [])],
    ) for item in items]


def _character_timeline(transcript: list[TranscriptSegment]) -> tuple[str, list[dict[str, float | None]]]:
    """Expand word timestamps into normalized character timestamps.

    Whisper sometimes groups several Chinese characters into one word.  Dividing
    that word's time proportionally gives deterministic cue switch points while
    retaining genuine pauses between words.
    """
    text_parts: list[str] = []
    timings: list[dict[str, float | None]] = []
    for segment in transcript:
        words = segment.words or [TranscriptWord(segment.text, segment.start, segment.end, segment.confidence)]
        for word in words:
            normalized = normalize_text(word.text)
            if not normalized:
                continue
            duration = max(0.0, word.end - word.start)
            count = len(normalized)
            for index, char in enumerate(normalized):
                text_parts.append(char)
                timings.append({
                    "start": word.start + duration * index / count,
                    "end": word.start + duration * (index + 1) / count,
                    "confidence": word.confidence,
                })
    return "".join(text_parts), timings


def match_body_cues(expected: list[tuple[str, str]], transcript: list[TranscriptSegment], max_chars: int = 10, minimum_similarity: float = 0.72) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match ordered Body rows and return word-timed subtitle cues."""
    actual, timings = _character_timeline(transcript)
    cursor = 0
    matches: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        from rapidfuzz.fuzz import ratio
    except ImportError:
        ratio = lambda a, b: 100 if a == b else 0

    for index, (row_key, text) in enumerate(expected):
        wanted = normalize_text(text)
        if not wanted:
            failures.append({"index": index, "row_key": row_key, "expected": text, "reason": "文案为空"})
            continue
        best: tuple[float, int, int, str] | None = None
        minimum_length = max(1, int(len(wanted) * 0.7))
        maximum_length = min(len(actual) - cursor, max(minimum_length, int(len(wanted) * 1.4) + 2))
        # The narration is required to follow Excel order, so every row starts at
        # the current cursor.  Only its end is searched.
        for length in range(minimum_length, maximum_length + 1):
            candidate = actual[cursor:cursor + length]
            score = ratio(wanted, candidate) / 100.0
            ranked = (score, -abs(length - len(wanted)), length, candidate)
            if best is None or ranked > best:
                best = ranked
        if best is None or best[0] < minimum_similarity:
            recognized = actual[cursor:cursor + max(1, len(wanted))]
            failures.append({
                "index": index, "row_key": row_key, "expected": text,
                "recognized": recognized,
                "time_range": [timings[cursor]["start"], timings[min(len(timings) - 1, cursor + max(0, len(recognized) - 1))]["end"]] if timings and cursor < len(timings) else None,
                "similarity": round(best[0], 4) if best else 0.0,
                "reason": "无法按 Body 顺序匹配 ASR 文本",
            })
            continue
        length, recognized = best[2], best[3]
        actual_start, actual_end = cursor, cursor + length
        row_start = float(timings[actual_start]["start"])
        row_end = float(timings[actual_end - 1]["end"])
        cue_items: list[SubtitleCue] = []
        consumed_expected = 0
        for cue_text in split_subtitle_text(text, max_chars):
            cue_normalized = normalize_text(cue_text)
            if not cue_normalized:
                continue
            expected_start = consumed_expected
            expected_end = min(len(wanted), expected_start + len(cue_normalized))
            consumed_expected = expected_end
            # Project Excel-character boundaries onto the recognized character
            # window.  Each recognized character already owns a word-derived time.
            cue_start_index = actual_start + min(length - 1, int(expected_start * length / max(1, len(wanted))))
            cue_end_index = actual_start + min(length, max(1, int(math.ceil(expected_end * length / max(1, len(wanted)))))) - 1
            cue_confidences = [timings[pos]["confidence"] for pos in range(cue_start_index, cue_end_index + 1) if timings[pos]["confidence"] is not None]
            cue_items.append(SubtitleCue(
                row_key=row_key,
                text=cue_text,
                start_seconds=float(timings[cue_start_index]["start"]),
                end_seconds=float(timings[cue_end_index]["end"]),
                confidence=sum(cue_confidences) / len(cue_confidences) if cue_confidences else None,
                matched=True,
            ))
        matches.append({
            "index": index, "row_key": row_key, "text": text,
            "start": row_start, "end": row_end, "recognized": recognized,
            "similarity": round(best[0], 4), "subtitle_cues": cue_items,
        })
        cursor = actual_end
    return matches, failures


def _contains(expected: str, actual: str) -> bool:
    return expected in actual or actual in expected


def match_transcript(expected: list[str], transcript: list[TranscriptSegment], minimum_similarity: float = 0.72) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match ordered Excel rows to ordered ASR segments; no silent fallback on mismatch."""
    remaining = ""
    start = None
    end = None
    cursor = 0
    matches: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        from rapidfuzz.fuzz import ratio
    except ImportError:
        ratio = lambda a, b: 100 if a == b else 0
    for index, text in enumerate(expected):
        wanted = normalize_text(text)
        if not wanted:
            failures.append({"index": index, "expected": text, "reason": "文案为空"})
            continue
        found = False
        candidate = ""
        candidate_start = None
        candidate_end = None
        for end_index in range(cursor, len(transcript)):
            piece = normalize_text(transcript[end_index].text)
            if candidate_start is None:
                candidate_start = transcript[end_index].start
            candidate_end = transcript[end_index].end
            candidate += piece
            score = ratio(wanted, candidate) / 100.0
            close_enough_prefix = _contains(wanted, candidate) and len(candidate) >= max(1, int(len(wanted) * 0.8))
            enough_text = len(candidate) >= max(1, int(len(wanted) * 0.8))
            if close_enough_prefix or (enough_text and score >= minimum_similarity):
                matches.append({"index": index, "text": text, "start": candidate_start, "end": candidate_end, "recognized": candidate, "similarity": round(score, 4)})
                cursor = end_index + 1
                found = True
                break
            if len(candidate) > len(wanted) * 2.2:
                break
        if not found:
            failures.append({"index": index, "expected": text, "recognized": candidate, "reason": "无法按顺序匹配 ASR 文本"})
    return matches, failures
