"""Deterministic WebVTT and SubRip exports for timestamped transcripts."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from .models import TextStrataItem, is_valid_id


_TIMESTAMP_RE = re.compile(
    r"(?:(?P<hours>\d{1,}):)?(?P<minutes>\d{1,2}):(?P<seconds>\d{2})"
    r"(?:[.,](?P<millis>\d{1,3}))?"
)
_TIMING_LINE_RE = re.compile(
    r"^(?P<start>(?:\d{1,}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)\s+-->\s+"
    r"(?P<end>(?:\d{1,}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)(?:\s+.*)?$"
)
_MARKDOWN_CUE_RE = re.compile(
    r"^\[(?P<start>(?:\d{1,}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
    r"(?:\s+-->\s+(?P<end>(?:\d{1,}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?))?\]\s+"
    r"(?P<text>.+)$"
)


@dataclass(frozen=True)
class CaptionCue:
    start_ms: int
    text: str
    end_ms: int | None = None


@dataclass(frozen=True)
class CaptionSource:
    """Metadata returned by a caption adapter alongside normalized cues."""

    language: str | None = None
    origin: str | None = None  # manual, automatic, or local
    status: str = "available"  # available, unavailable, or failed
    tool: str | None = None


def _parse_timestamp(value: str) -> int:
    match = _TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid caption timestamp: {value}")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    if minutes > 59 or seconds > 59:
        raise ValueError(f"invalid caption timestamp: {value}")
    millis_raw = match.group("millis") or "0"
    millis = int(millis_raw.ljust(3, "0"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _format_timestamp(value_ms: int, separator: str) -> str:
    value_ms = max(0, int(value_ms))
    hours, remainder = divmod(value_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _plain_cue_text(lines: list[str]) -> str:
    value = " ".join(line.strip() for line in lines if line.strip())
    value = re.sub(r"<[^>]*>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_webvtt(value: str) -> list[CaptionCue]:
    """Parse caption cues from WebVTT while preserving valid end times."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues: list[CaptionCue] = []
    for block in re.split(r"\n[ \t]*\n", normalized):
        lines = [line.rstrip() for line in block.splitlines()]
        timing_index = next(
            (index for index, line in enumerate(lines) if _TIMING_LINE_RE.fullmatch(line.strip())),
            None,
        )
        if timing_index is None:
            continue
        timing = _TIMING_LINE_RE.fullmatch(lines[timing_index].strip())
        if timing is None:
            continue
        text = _plain_cue_text(lines[timing_index + 1 :])
        if not text:
            continue
        start_ms = _parse_timestamp(timing.group("start"))
        end_ms = _parse_timestamp(timing.group("end"))
        cues.append(CaptionCue(start_ms=start_ms, end_ms=end_ms, text=text))
    return cues


def parse_markdown_transcript(body: str) -> list[CaptionCue]:
    """Read cues from a ``Timestamped transcript`` Markdown section."""
    cues: list[CaptionCue] = []
    in_transcript = False
    for line in body.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            in_transcript = heading.group(1).strip().lower() == "timestamped transcript"
            continue
        if not in_transcript:
            continue
        match = _MARKDOWN_CUE_RE.match(line.strip())
        if not match:
            continue
        start_ms = _parse_timestamp(match.group("start"))
        end_ms = _parse_timestamp(match.group("end")) if match.group("end") else None
        cues.append(CaptionCue(start_ms=start_ms, end_ms=end_ms, text=match.group("text").strip()))
    return cues


def normalize_cues(cues: list[CaptionCue]) -> list[CaptionCue]:
    """Normalize cue text, collapse rolling-caption repeats, and clamp timing.

    Some caption providers emit a rolling window several times at the same
    timestamp: ``"hello"``, then ``"hello world"``. Those are one spoken
    utterance, not meaningful repeated speech. Only prefix-equivalent cues at
    the same start (or within a short provider rollover window) are collapsed; identical
    words spoken again later remain intact.
    """
    ordered = sorted(
        (replace(cue, text=re.sub(r"\s+", " ", cue.text).strip()) for cue in cues if cue.text.strip() and cue.start_ms >= 0),
        key=lambda cue: (cue.start_ms, cue.end_ms if cue.end_ms is not None else 2**63, cue.text),
    )
    normalized: list[CaptionCue] = []
    for cue in ordered:
        replacement_index: int | None = None
        for index in range(len(normalized) - 1, -1, -1):
            previous = normalized[index]
            if cue.start_ms - previous.start_ms > 250:
                break
            if cue.text == previous.text or cue.text.startswith(previous.text + " "):
                replacement_index = index
                break
            if previous.text.startswith(cue.text + " "):
                replacement_index = -1
                break
        if replacement_index == -1:
            continue
        if replacement_index is not None:
            normalized[replacement_index] = cue
            continue
        normalized.append(cue)
    bounded: list[CaptionCue] = []
    for index, cue in enumerate(normalized):
        next_start = normalized[index + 1].start_ms if index + 1 < len(normalized) else None
        end_ms = cue.end_ms
        if end_ms is None or end_ms <= cue.start_ms:
            end_ms = next_start if next_start is not None and next_start > cue.start_ms else cue.start_ms + 3000
        if next_start is not None and end_ms > next_start:
            end_ms = next_start
        if end_ms <= cue.start_ms:
            end_ms = cue.start_ms + 1
        bounded.append(replace(cue, end_ms=end_ms))
    return bounded


def render_webvtt(cues: list[CaptionCue]) -> str:
    normalized = normalize_cues(cues)
    if not normalized:
        return "WEBVTT\n\n"
    blocks = []
    for cue in normalized:
        start = _format_timestamp(cue.start_ms, ".")
        end = _format_timestamp(cue.end_ms or cue.start_ms + 3000, ".")
        blocks.append(f"{start} --> {end}\n{html.escape(cue.text, quote=False)}")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def render_srt(cues: list[CaptionCue]) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(normalize_cues(cues), start=1):
        start = _format_timestamp(cue.start_ms, ",")
        end = _format_timestamp(cue.end_ms or cue.start_ms + 3000, ",")
        blocks.append(f"{index}\n{start} --> {end}\n{cue.text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def transcript_markdown(cues: list[CaptionCue]) -> str:
    lines = []
    for cue in normalize_cues(cues):
        total_seconds = cue.start_ms // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        stamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        lines.append(f"[{stamp}] {cue.text}")
    return "\n".join(lines)


def is_youtube_transcript(item: TextStrataItem) -> bool:
    source = urlparse(item.provenance.source_url or "")
    host = (source.hostname or "").lower()
    is_youtube = host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com"
    return is_youtube and bool(parse_markdown_transcript(item.body))


def has_timestamped_transcript(item: TextStrataItem) -> bool:
    """True for any item carrying exportable cues: YouTube captions or local audio transcription."""
    if is_youtube_transcript(item):
        return True
    tags = {tag.lower() for tag in item.tags}
    return "transcript" in tags and bool(parse_markdown_transcript(item.body))


def _artifact_dir(root: Path) -> Path:
    return Path(root) / ".fabric" / "caption-exports"


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_caption_artifacts(root: Path, note_id: str, cues: list[CaptionCue], source_body: str) -> None:
    if not is_valid_id(note_id):
        raise ValueError("invalid TextStrata item ID")
    directory = _artifact_dir(root)
    _atomic_write(directory / f"{note_id}.vtt", render_webvtt(cues))
    _atomic_write(directory / f"{note_id}.srt", render_srt(cues))
    manifest = {
        "note_id": note_id,
        "source_sha256": hashlib.sha256(source_body.encode("utf-8")).hexdigest(),
        "cue_count": len(normalize_cues(cues)),
    }
    _atomic_write(directory / f"{note_id}.json", json.dumps(manifest, indent=2) + "\n")


def export_caption(root: Path, item: TextStrataItem, format_name: str) -> str:
    if format_name not in {"vtt", "srt"}:
        raise ValueError("unsupported caption format")
    if not has_timestamped_transcript(item):
        raise ValueError("item does not contain a timestamped transcript")

    directory = _artifact_dir(root)
    manifest_path = directory / f"{item.id}.json"
    artifact_path = directory / f"{item.id}.{format_name}"
    source_hash = hashlib.sha256(item.body.encode("utf-8")).hexdigest()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_sha256") == source_hash and artifact_path.is_file():
            return artifact_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass

    cues = parse_markdown_transcript(item.body)
    return render_webvtt(cues) if format_name == "vtt" else render_srt(cues)
