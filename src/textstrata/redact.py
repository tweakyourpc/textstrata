"""Deterministic PII detection and redaction.

Pattern-based scanning runs first (high precision, no model calls). An optional
AI-assisted scan (via Ollama) catches patterns the regexes miss. Results are
merged and deduplicated before redaction is applied.

Usage::

    findings = scan_patterns(body)
    sanitized = redact(body, findings, strategy="placeholder")
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

CONTEXT_WINDOW = 40

RedactStrategy = Literal["placeholder", "mask", "remove"]

_PATTERNS: dict[str, re.Pattern] = {
    "ssn": re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+(?:\.\w+)*"),
    "phone": re.compile(
        r"(?:\+?\d{1,3}[-.\s]?\(?\d{3,4}\)?(?:[-.\s]\d{3,4})?[-.\s]?\d{4,}"
        r"|\b\d{3}[-.]\d{4}\b)"
        r"(?:\s*(?:ext|x)\s*\d{1,5})?"
    ),
    "credit_card": re.compile(
        r"\b(?:(?:4\d{3}|5[1-5]\d{2}|6(?:011|5\d{2})|3[47]\d{2})[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}|3(?:0[0-5]|[68]\d)\d{11})\b"
    ),
    "api_key": re.compile(
        r"(?i)"
        r"(?:sk-[a-zA-Z0-9-]{6,80}|"                      # OpenAI
        r"gh[opsu]_[a-zA-Z0-9]{30,40}|"                   # GitHub
        r"AIza[0-9A-Za-z_-]{35}|"                         # Google API
        r"AKIA[0-9A-Z]{16}|"                              # AWS access key
        r"SG\.[a-zA-Z0-9]{22}\.[a-zA-Z0-9]{43}|"         # SendGrid
        r"(?:pk|rk_live|sk_live)_[a-zA-Z0-9]{24,53}|"    # Stripe
        r"xox[bpsa]-[a-zA-Z0-9-]{10,80})"                # Slack
        r"(?!\w)"
    ),
    "ip_address": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
    ),
    "url_credential": re.compile(r"https?://[^:/\s]+:[^@\s]+@\S+"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


@dataclass(frozen=True)
class PIIFinding:
    """One detected PII instance in a body of text."""

    category: str
    raw: str
    start: int
    end: int
    confidence: float
    suggested_replacement: str
    context: str = ""


_REPLACEMENTS: dict[str, str] = {
    "email": "[REDACTED_EMAIL]",
    "phone": "[REDACTED_PHONE]",
    "ssn": "[REDACTED_SSN]",
    "credit_card": "[REDACTED_CC]",
    "api_key": "[REDACTED_API_KEY]",
    "ip_address": "[REDACTED_IP]",
    "url_credential": "[REDACTED_URL_CRED]",
    "aws_key": "[REDACTED_AWS_KEY]",
}


def _extract_context(text: str, start: int, end: int) -> str:
    before = text[max(0, start - CONTEXT_WINDOW) : start]
    after = text[end : min(len(text), end + CONTEXT_WINDOW)]
    before = before.replace("\n", " ").strip()
    after = after.replace("\n", " ").strip()
    ctx = before + "<HIT>" + after
    if len(ctx) > CONTEXT_WINDOW * 2 + 10:
        return "..." + ctx[-(CONTEXT_WINDOW * 2 + 5):]
    return ctx


def scan_patterns(text: str) -> list[PIIFinding]:
    """Run all deterministic regex patterns and return sorted, deduplicated findings."""
    raw_findings: list[PIIFinding] = []

    for category, pattern in _PATTERNS.items():
        for m in pattern.finditer(text):
            raw = m.group()
            start = m.start()
            end = m.end()
            confidence = _confidence(category, raw)
            replacement = _REPLACEMENTS.get(category, "[REDACTED]")
            finding = PIIFinding(
                category=category,
                raw=raw,
                start=start,
                end=end,
                confidence=confidence,
                suggested_replacement=replacement,
                context=_extract_context(text, start, end),
            )
            raw_findings.append(finding)

    raw_findings.sort(key=lambda f: (f.start, -f.end))
    return _deduplicate(raw_findings)


def _confidence(category: str, raw: str) -> float:
    if category == "ssn":
        return 0.98
    if category == "aws_key":
        return 0.99
    if category == "credit_card":
        return 0.90
    if category == "email":
        return 0.95
    if category == "url_credential":
        return 0.95
    if category == "api_key":
        return 0.92
    if category == "phone":
        return 0.85
    if category == "ip_address":
        return 0.80
    return 0.70


def _deduplicate(findings: list[PIIFinding]) -> list[PIIFinding]:
    """Merge overlapping findings, keeping the higher-confidence one."""
    if not findings:
        return []
    merged: list[PIIFinding] = [findings[0]]
    for f in findings[1:]:
        prev = merged[-1]
        if f.start < prev.end:
            if f.confidence > prev.confidence and f.end >= prev.end:
                merged[-1] = f
            continue
        merged.append(f)
    return merged


def scan(text: str, *, ai_assist: bool = False) -> list[PIIFinding]:
    """Orchestrator: run pattern scan and optionally AI-assisted scan.

    ``ai_assist`` is a placeholder for future Ollama integration.
    Currently only pattern-based scanning is implemented.
    """
    findings = scan_patterns(text)
    if ai_assist:
        pass
    return findings


def redact(
    text: str,
    findings: list[PIIFinding],
    strategy: RedactStrategy = "placeholder",
) -> str:
    """Apply redactions to *text* using the given *strategy*.

    Strategies:

    * ``"placeholder"`` — replace each match with a category label
    * ``"mask"`` — show first/last character with asterisks in between
    * ``"remove"`` — delete the matched text entirely
    """
    if not findings:
        return text
    findings = sorted(findings, key=lambda f: f.start, reverse=True)
    parts = list(text)
    for f in findings:
        replacement = _redact_value(f.raw, f.category, strategy)
        parts[f.start : f.end] = replacement
    return "".join(parts)


def _redact_value(raw: str, category: str, strategy: RedactStrategy) -> str:
    if strategy == "remove":
        return ""
    if strategy == "mask":
        if len(raw) <= 3:
            return raw[0] + "*" * (len(raw) - 1)
        _preserve = {".", "@", "-", "_", "+", "#", "!"}
        parts = list(raw)
        n = len(parts)
        keep_front = max(1, n // 6)
        keep_back = max(1, n // 6)
        for i in range(n):
            if i < keep_front:
                continue
            if i >= n - keep_back:
                continue
            if parts[i] in _preserve:
                continue
            parts[i] = "*"
        return "".join(parts)
    return _REPLACEMENTS.get(category, "[REDACTED]")
