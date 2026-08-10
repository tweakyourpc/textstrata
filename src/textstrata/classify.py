"""Deterministic classification, tag suggestion, and policy suggestion.

Per the architecture note, ingestion is policy-driven and deterministic before
it becomes AI-assisted. Nothing here calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .models import ContentType, HandlingMode, PreservationMode

# Ordered rules: (ContentType, compiled signal). First match wins.
_CLASS_RULES: list[tuple[ContentType, re.Pattern]] = [
    (ContentType.INCIDENT, re.compile(r"\b(incident|symptom|root cause|steps to reproduce|affected systems)\b", re.I)),
    (ContentType.KNOWN_ERROR, re.compile(r"\b(known error|known issue|permanent fix|kba\b|known bug)\b", re.I)),
    (ContentType.DECISION_RECORD, re.compile(r"\b(decision record|adr|we decided|status:\s*accepted)\b", re.I)),
    (ContentType.ANTI_PATTERN, re.compile(r"\banti[- ]?pattern\b", re.I)),
    (ContentType.ARCHITECTURE_NOTE, re.compile(r"\barchitect(ure|ural)\b", re.I)),
    (ContentType.PROMPT_TEMPLATE, re.compile(r"\b(prompt template|system prompt|\{\{[a-z_]+\}\})\b", re.I)),
    (ContentType.COMMAND_RECIPE, re.compile(r"```(?:bash|sh|shell|console)\b", re.I)),
    (ContentType.CODE_SAMPLE, re.compile(r"```[a-z0-9+]+\n", re.I)),
    (ContentType.STYLE_GUIDE, re.compile(r"\bstyle guide\b", re.I)),
    (ContentType.STANDARD, re.compile(r"\b(standard|convention|MUST|SHALL)\b")),
    (ContentType.PLAYBOOK, re.compile(r"\b(playbook|runbook|step \d)\b", re.I)),
    (ContentType.POLICY, re.compile(r"\bpolic(y|ies)\b", re.I)),
]

# Fixed taxonomy for tag suggestion. Each tag maps to a signal; a match adds
# the tag. Suggestions never remove or override author-declared tags.
_TAG_RULES: dict[str, re.Pattern] = {
    "code": re.compile(r"```[a-z0-9+]+\n", re.I),
    "shell": re.compile(r"```(?:bash|sh|shell|console)\b", re.I),
    "python": re.compile(r"```python\b|(?<![\w.])def \w+\(", re.I),
    "mcp": re.compile(r"\bmcp\b", re.I),
    "rag": re.compile(r"\b(rag|retrieval[- ]augmented)\b", re.I),
    "accessibility": re.compile(r"\b(accessib|wcag|aria|keyboard nav)\b", re.I),
    "security": re.compile(r"\b(ssrf|csp|sanitiz|content security policy)\b", re.I),
    "taxonomy": re.compile(r"\b(taxonom|ontolog|schema)\b", re.I),
    "networking": re.compile(
        r"\b(dns|dhcp|vpn|vlan|tcp/ip|ethernet|subnet|firewall|wifi|wi-fi|"
        r"ip address|network cable|network switch|network router|"
        r"home network|local network|port forwarding|network outage|"
        r"no internet connection|network connectivity)\b",
        re.I,
    ),
    "hardware": re.compile(
        r"\b(computer hardware|pc hardware|printer|barcode scanner|"
        r"document scanner|docking station|bios|firmware|peripheral device|"
        r"external monitor|hardware failure|hardware fault)\b",
        re.I,
    ),
    "email": re.compile(
        r"\b(outlook|exchange server|microsoft exchange|spam filter|"
        r"spam folder|junk email|phish|mailbox|smtp|imap|email account|"
        r"email server|email client)\b",
        re.I,
    ),
    "account-access": re.compile(
        r"\b(user account|account lockout|account access|account locked|"
        r"login credentials|login issue|failed login|log ?in fail|"
        r"password reset|sso login|mfa|2fa|two-factor|authentication "
        r"failure|access denied|permission denied|credential)\b",
        re.I,
    ),
    "software": re.compile(
        r"\b(software update|software install|software license|license "
        r"key|install(?:ation)? (?:fails?|failed|error)|patch fails?|"
        r"upgrade fails?|application crash|program (?:crash|freeze)|"
        r"system hang|software bug)\b",
        re.I,
    ),
    "voip": re.compile(
        r"\b(voip|sip trunk|softphone|pbx|voicemail|dial tone|"
        r"phone system|caller id|call forwarding|extension number|"
        r"ip phone|internet telephony)\b",
        re.I,
    ),
    "known-error": re.compile(r"\b(known error|known issue|permanent fix|workaround)\b", re.I),
}


@dataclass(frozen=True)
class PolicySuggestion:
    handling: HandlingMode
    preservation: PreservationMode
    rationale: str


def detect_type(explicit: object, title: str, body: str) -> ContentType:
    """Explicit ``type`` wins; otherwise fall back to structural rules."""
    coerced = ContentType.coerce(explicit)
    if coerced is not ContentType.NOTE or explicit:
        if explicit and coerced is not ContentType.NOTE:
            return coerced
    haystack = f"{title}\n{body}"
    for content_type, signal in _CLASS_RULES:
        if signal.search(haystack):
            return content_type
    return coerced


def suggest_tags(title: str, body: str, declared: list[str]) -> list[str]:
    """Return *new* tags implied by the text, excluding ones already declared."""
    haystack = f"{title}\n{body}"
    have = {t.lower() for t in declared}
    found: list[str] = []
    for tag, signal in _TAG_RULES.items():
        if tag not in have and signal.search(haystack):
            found.append(tag)
    return found


def suggest_policy(content_type: ContentType, title: str, body: str) -> PolicySuggestion:
    """Suggest a handling/preservation mode without mutating the item."""
    haystack = f"{title}\n{body}"
    if re.search(r"\b(advertisement|sponsor|subscribe|follow me)\b", haystack, re.I):
        return PolicySuggestion(
            HandlingMode.AI_ONLY_EYES,
            PreservationMode.REMOVE_FLUFF_ALLOWED,
            "source looks like noisy promotional material",
        )
    if content_type in (ContentType.CODE_SAMPLE, ContentType.COMMAND_RECIPE):
        return PolicySuggestion(
            HandlingMode.HUMAN_ONLY,
            PreservationMode.PRESERVE_EXACT,
            "code and command material should stay exact",
        )
    if content_type in (ContentType.STANDARD, ContentType.POLICY, ContentType.STYLE_GUIDE):
        return PolicySuggestion(
            HandlingMode.HUMAN_ONLY,
            PreservationMode.PRESERVE_EXACT,
            "normative material should be preserved exactly",
        )
    if content_type in (ContentType.INCIDENT, ContentType.KNOWN_ERROR):
        return PolicySuggestion(
            HandlingMode.HUMAN_PLUS_AI,
            PreservationMode.PRESERVE_EXACT,
            "incident records should be preserved exactly for audit trail",
        )
    if content_type in (
        ContentType.ARCHITECTURE_NOTE,
        ContentType.REFERENCE,
        ContentType.DECISION_RECORD,
        ContentType.PROMPT_TEMPLATE,
        ContentType.PLAYBOOK,
        ContentType.ANTI_PATTERN,
    ):
        return PolicySuggestion(
            HandlingMode.HUMAN_PLUS_AI,
            PreservationMode.SUMMARIZE_ALLOWED,
            "reference-style material may be summarized while keeping the source",
        )
    if len(body.split()) > 800:
        return PolicySuggestion(
            HandlingMode.AUTO_SANITIZE_THEN_REVIEW,
            PreservationMode.REMOVE_FLUFF_ALLOWED,
            "long prose is a candidate for sanitization before review",
        )
    return PolicySuggestion(
        HandlingMode.HUMAN_PLUS_AI,
        PreservationMode.SUMMARIZE_ALLOWED,
        "default to human-plus-ai review for prose material",
    )
