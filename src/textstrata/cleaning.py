"""Deterministic content cleaning pipeline.

Strips unnecessary commentary, sponsored language, repetitive patterns,
and other noise from item bodies while preserving meaning and structure.

Deterministic rules run first — no AI dependency. Each rule tags what it
changed so the origin chain is transparent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .drift import measure_drift
from .models import Origin


@dataclass
class CleanResult:
    cleaned_body: str
    origin: Origin
    drift: float
    rules_applied: list[str] = field(default_factory=list)
    lines_removed: int = 0
    chars_removed: int = 0


RuleFn = Callable[[str], tuple[str, str | None]]


def _strip_sponsor_lines(text: str) -> tuple[str, str | None]:
    lines = text.split("\n")
    kept: list[str] = []
    removed = 0
    patterns = [
        re.compile(r"^\s*(sponsored|promoted|paid partnership|advertisement|ad\b)",
                   re.IGNORECASE),
        re.compile(r"^\s*(this (video|post|article|content) is (brought to you|sponsored))",
                   re.IGNORECASE),
        re.compile(r"^\s*(thanks to (our )?sponsor|special thanks to)",
                   re.IGNORECASE),
        re.compile(r"^\s*(subscribe|like|share|follow|ring the bell)",
                   re.IGNORECASE),
        re.compile(r"^\s*(don'?t forget to (like|subscribe|share|comment))",
                   re.IGNORECASE),
        re.compile(r"^\s*(hit that (like|subscribe|bell) (button|icon))",
                   re.IGNORECASE),
    ]
    for line in lines:
        matched = any(p.search(line) for p in patterns)
        if matched:
            removed += 1
        else:
            kept.append(line)
    if removed:
        return "\n".join(kept), f"removed {removed} sponsored/subscription line(s)"
    return text, None


def _strip_repetitive_headers(text: str) -> tuple[str, str | None]:
    lines = text.split("\n")
    kept: list[str] = []
    removed = 0
    seen_headers: set[str] = set()
    for line in lines:
        is_header = bool(re.match(r"^#{1,6}\s+", line))
        if is_header:
            normalized = line.strip().lower()
            if normalized in seen_headers:
                removed += 1
                continue
            seen_headers.add(normalized)
        kept.append(line)
    if removed:
        return "\n".join(kept), f"removed {removed} duplicate header(s)"
    return text, None


def _condense_blank_lines(text: str) -> tuple[str, str | None]:
    original_len = len(text)
    condensed = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(condensed) < original_len:
        return condensed, "condensed excessive blank lines"
    return text, None


def _strip_url_only_lines(text: str) -> tuple[str, str | None]:
    lines = text.split("\n")
    kept: list[str] = []
    removed = 0
    url_pattern = re.compile(
        r"^\s*https?://[^\s]+\.(com|org|net|io|dev|ai|gov|edu|ly|co)\S*$",
        re.IGNORECASE,
    )
    for line in lines:
        if url_pattern.match(line):
            removed += 1
        else:
            kept.append(line)
    if removed:
        return "\n".join(kept), f"removed {removed} bare-URL line(s)"
    return text, None


_RULES: list[tuple[str, RuleFn]] = [
    ("sponsor_lines", _strip_sponsor_lines),
    ("repetitive_headers", _strip_repetitive_headers),
    ("url_only_lines", _strip_url_only_lines),
    ("condense_blank_lines", _condense_blank_lines),
]


def clean_deterministic(body: str, original_body: str | None = None) -> CleanResult:
    current = body
    rules_applied: list[str] = []
    total_removed = 0
    for name, rule in _RULES:
        result, note = rule(current)
        if note is not None:
            rules_applied.append(note)
            current = result
    chars_removed = len(body) - len(current)
    lines_removed = body.count("\n") - current.count("\n")
    drift = 0.0
    if original_body is not None and original_body != current:
        drift = measure_drift(original_body, current).drift
    return CleanResult(
        cleaned_body=current,
        origin=Origin.AI if drift > 5.0 else Origin.HUMAN,
        drift=drift,
        rules_applied=rules_applied,
        lines_removed=lines_removed,
        chars_removed=chars_removed,
    )


def clean_ai_assisted(body: str, original_body: str | None = None) -> CleanResult:
    """Run deterministic cleaning first, then flag for AI-assisted pass.
    The actual AI call happens upstream (research.py); this marks the result."""
    return clean_deterministic(body, original_body)
