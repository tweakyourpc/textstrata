"""Generic deterministic source records and adapter contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


def normalize_text(value: str) -> str:
    """Normalize source text for stable hashing and Markdown ingestion."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+$", "", line) for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


@dataclass(frozen=True)
class NormalizedSourceRecord:
    """Source-neutral content ready for the TextStrata ingestion pipeline."""

    source_type: str
    source_id: str
    external_id: str
    title: str
    content: str
    created_at: str | None = None
    updated_at: str | None = None
    tags: tuple[str, ...] = ()
    topic: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self) -> None:
        content = normalize_text(self.content)
        object.__setattr__(self, "content", content)
        if not self.content_hash:
            object.__setattr__(self, "content_hash", hashlib.sha256(content.encode("utf-8")).hexdigest())


class SourceAdapter(Protocol):
    """Minimal contract for deterministic source integrations."""

    def discover(self) -> Iterable[NormalizedSourceRecord]: ...

    def fetch(self, external_id: str) -> NormalizedSourceRecord: ...

    def normalize(self, record: Any) -> NormalizedSourceRecord: ...

    def fingerprint(self, record: NormalizedSourceRecord) -> str: ...
