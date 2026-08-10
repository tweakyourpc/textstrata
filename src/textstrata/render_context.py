"""Shared render context types independent of presentation package wiring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .models import TextStrataItem


@dataclass(frozen=True)
class RenderContext:
    title: str
    item: TextStrataItem
    validation_errors: list[str]
    validation_warnings: list[str]
    suggested_tags: list[str]
    policy_handling: str | None = None
    policy_preservation: str | None = None
    policy_reason: str | None = None
    outgoing_links: Sequence[object] = ()
    known_ids: frozenset[str] | set[str] = frozenset()
    similar_ids: Sequence[str] = ()
    knowledge_score: float | None = None
    community: str | None = None
    raw_markdown: str | None = None
    incoming_links: Sequence[object] = ()
    why_related: Mapping[str, str] = field(default_factory=dict)
    item_titles: Mapping[str, str] = field(default_factory=dict)
    item_aliases: Mapping[str, str] = field(default_factory=dict)
