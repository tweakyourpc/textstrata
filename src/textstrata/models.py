"""Typed content core for TextStrata.

This is deliberately a *minimal, extensible* core rather than the full schema
from the architecture note. Every item carries a small set of typed,
validated fields plus an open ``extra`` map, so unproven metadata can ride
along as data until it earns a promotion to a first-class field. Schema-first
tends to calcify; item-first tends to converge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

VALID_CONTRIBUTORS = frozenset({"via_script", "human", "via_ai"})

def parse_contributor_chain(chain: str) -> list[str]:
    return [c.strip() for c in chain.split(",") if c.strip()]

def append_contributor(chain: str, contributor: str) -> str:
    if contributor not in VALID_CONTRIBUTORS:
        return chain
    parts = parse_contributor_chain(chain)
    if not parts or parts[-1] != contributor:
        parts.append(contributor)
    return ", ".join(parts)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContentType(str, Enum):
    """Canonical content classes. ``NOTE`` is the safe default fallback."""

    POLICY = "policy"
    PROMPT_TEMPLATE = "prompt_template"
    PLAYBOOK = "playbook"
    COMMAND_RECIPE = "command_recipe"
    STANDARD = "standard"
    STYLE_GUIDE = "style_guide"
    CODE_SAMPLE = "code_sample"
    ARCHITECTURE_NOTE = "architecture_note"
    REFERENCE = "reference"
    ANTI_PATTERN = "anti_pattern"
    DECISION_RECORD = "decision_record"
    INCIDENT = "incident"
    KNOWN_ERROR = "known_error"
    NOTE = "note"

    @classmethod
    def coerce(cls, value: object) -> "ContentType":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.NOTE
        key = re.sub(r"[\s-]+", "_", str(value).strip().lower())
        by_value = {m.value: m for m in cls}
        return by_value.get(key, cls.NOTE)


class HandlingMode(str, Enum):
    UNSET = ""
    HUMAN_ONLY = "human_only"
    HUMAN_PLUS_AI = "human_plus_ai"
    AI_ONLY_EYES = "ai_only_eyes"
    AUTO_SANITIZE_THEN_REVIEW = "auto_sanitize_then_review"

    @classmethod
    def coerce(cls, value: object) -> "HandlingMode":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.UNSET
        key = re.sub(r"[\s-]+", "_", str(value).strip().lower())
        return {m.value: m for m in cls}.get(key, cls.UNSET)


class PreservationMode(str, Enum):
    PRESERVE_EXACT = "preserve_exact"
    SUMMARIZE_ALLOWED = "summarize_allowed"
    REMOVE_FLUFF_ALLOWED = "remove_fluff_allowed"
    TAG_ONLY = "tag_only"
    REWRITE_ALLOWED = "rewrite_allowed"

    @classmethod
    def coerce(cls, value: object) -> "PreservationMode":
        if isinstance(value, cls):
            return value
        key = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
        return {m.value: m for m in cls}.get(key, cls.PRESERVE_EXACT)


class Origin(str, Enum):
    """What produced or edited this item."""

    HUMAN = "human"
    AI = "ai"
    COMBINED = "combined"
    SUMMARIZED = "summarized"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, value: object) -> "Origin":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.UNKNOWN
        key = str(value).strip().lower()
        by_value = {m.value: m for m in cls}
        return by_value.get(key, cls.UNKNOWN)


@dataclass
class EditRecord:
    """A single edit applied to an item, tracking origin and drift."""

    origin: Origin = Origin.UNKNOWN
    timestamp: str = field(default_factory=_utcnow)
    drift: float = 0.0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "origin": self.origin.value,
            "timestamp": self.timestamp,
            "drift": self.drift,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditRecord":
        return cls(
            origin=Origin.coerce(data.get("origin")),
            timestamp=data.get("timestamp", _utcnow()),
            drift=float(data.get("drift", 0.0)),
            description=str(data.get("description", "")),
        )


@dataclass
class Provenance:
    """Where an item came from and what created it."""

    created_via: str | None = None
    authorship: str | None = None
    origin: Origin = Origin.UNKNOWN
    ai_processing: str = "none"
    source_url: str | None = None
    source_kind: str | None = None
    source_identity: str | None = None
    caption_language: str | None = None
    caption_origin: str | None = None
    acquisition_tool: str | None = None
    ingested_at: str = field(default_factory=_utcnow)
    contributor_chain: str = ""
    ai_vendor: str | None = None
    ai_model: str | None = None
    ai_operation: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "created_via": self.created_via,
            "authorship": self.authorship,
            "ai_processing": self.ai_processing,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "source_identity": self.source_identity,
            "caption_language": self.caption_language,
            "caption_origin": self.caption_origin,
            "acquisition_tool": self.acquisition_tool,
            "ingested_at": self.ingested_at,
        }
        if self.origin != Origin.UNKNOWN or True:
            d["origin"] = self.origin.value
        if self.ai_vendor:
            d["ai_vendor"] = self.ai_vendor
        if self.ai_model:
            d["ai_model"] = self.ai_model
        if self.ai_operation:
            d["ai_operation"] = self.ai_operation
        if self.contributor_chain:
            d["contributor_chain"] = self.contributor_chain
        return d


_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


@dataclass
class TextStrataItem:
    """A single typed, policy-aware unit of the textstrata."""

    id: str
    type: ContentType
    title: str
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    handling: HandlingMode = HandlingMode.UNSET
    preservation: PreservationMode = PreservationMode.PRESERVE_EXACT
    retrieval_priority: int = 0
    provenance: Provenance = field(default_factory=Provenance)
    body: str = ""
    cleaned_body: str = ""
    edited_by: list[EditRecord] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def has_cleaned(self) -> bool:
        return bool(self.cleaned_body)

    @property
    def edit_count(self) -> int:
        return len(self.edited_by)

    def canonical_frontmatter(self) -> dict:
        data = {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "aliases": list(self.aliases),
            "tags": list(self.tags),
            "related": list(self.related),
            "dependencies": list(self.dependencies),
            "handling": self.handling.value,
            "preservation": self.preservation.value,
            "retrieval_priority": self.retrieval_priority,
            "provenance": {k: v for k, v in self.provenance.to_dict().items() if v is not None},
        }
        extra = dict(self.extra)
        if self.edited_by:
            extra["edited_by"] = [e.to_dict() for e in self.edited_by]
        if extra:
            data["extra"] = extra
        return {k: v for k, v in data.items() if v not in ([], {}, None, "")}


CONTRADICTORY_POLICY = {
    (HandlingMode.HUMAN_ONLY, PreservationMode.REWRITE_ALLOWED),
    (HandlingMode.HUMAN_ONLY, PreservationMode.SUMMARIZE_ALLOWED),
    (HandlingMode.HUMAN_ONLY, PreservationMode.REMOVE_FLUFF_ALLOWED),
}


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))
