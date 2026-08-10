"""The ingestion front door.

One deterministic pipeline, in the order the architecture note prescribes:

1. parse + merge all front-matter blocks (nothing dropped)
2. detect content class
3. suggest tags from rules and the existing taxonomy
4. attach handling / preservation policy
5. store the original verbatim, separate from any transformed output
6. validate; publish the normalized version only if it passes
7. return the result with its policy attached
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import activity, classify, frontmatter
from .models import (
    ContentType,
    TextStrataItem,
    HandlingMode,
    PreservationMode,
    Provenance,
)
from .store import TextStrataStore
from .validate import ValidationResult, validate

# Reserved keys are lifted into typed fields; everything else survives in
# ``extra`` so no author metadata is lost.
_RESERVED = {
    "id", "type", "title", "aliases", "tags", "related", "dependencies",
    "handling", "preservation", "retrieval_priority", "provenance",
    "created_via", "authorship", "ai_processing", "source_url", "extra",
    "contributor_chain", "ai_vendor", "ai_model", "ai_operation",
    "source_kind", "source_identity", "caption_language", "caption_origin", "acquisition_tool",
}


@dataclass
class IngestResult:
    item: TextStrataItem
    validation: ValidationResult
    published: bool
    original_path: Path | None = None
    normalized_path: Path | None = None
    suggested_tags: list[str] = field(default_factory=list)
    frontmatter_conflicts: list[str] = field(default_factory=list)
    had_stacked_frontmatter: bool = False


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "untitled"


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, set):
        return [str(v).strip() for v in sorted(value, key=str) if str(v).strip()]
    # A scalar is one value. Authors who need multiple values should use a
    # YAML sequence; splitting here corrupts legitimate values such as
    # ``Smith, Jr.``.
    scalar = str(value).strip()
    return [scalar] if scalar else []


def build_item(raw_text: str, *, fallback_id: str | None = None) -> tuple[TextStrataItem, list[str], frontmatter.MergedFrontmatter]:
    """Turn raw file text into a typed item plus suggested tags. Pure: no I/O."""
    fm = frontmatter.parse(raw_text)
    data = fm.data
    body = fm.body

    title = str(data.get("title") or "").strip()
    if not title:
        heading = re.search(r"^\s*#\s+(.+)$", body, re.MULTILINE)
        title = heading.group(1).strip() if heading else (fallback_id or "Untitled")

    item_id = str(data.get("id") or "").strip() or _slug(fallback_id or title)

    declared_tags = _as_list(data.get("tags"))
    suggested = classify.suggest_tags(title, body, declared_tags)
    tags = declared_tags + suggested

    content_type = classify.detect_type(data.get("type"), title, body)

    provenance_data = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    provenance = Provenance(
        created_via=data.get("created_via") or provenance_data.get("created_via"),
        authorship=data.get("authorship") or provenance_data.get("authorship"),
        ai_processing=str(data.get("ai_processing") or provenance_data.get("ai_processing") or "none"),
        source_url=data.get("source_url") or provenance_data.get("source_url"),
        source_kind=data.get("source_kind") or provenance_data.get("source_kind"),
        source_identity=data.get("source_identity") or provenance_data.get("source_identity"),
        caption_language=data.get("caption_language") or provenance_data.get("caption_language"),
        caption_origin=data.get("caption_origin") or provenance_data.get("caption_origin"),
        acquisition_tool=data.get("acquisition_tool") or provenance_data.get("acquisition_tool"),
        contributor_chain=data.get("contributor_chain") or provenance_data.get("contributor_chain") or "",
        ai_vendor=data.get("ai_vendor") or provenance_data.get("ai_vendor"),
        ai_model=data.get("ai_model") or provenance_data.get("ai_model"),
        ai_operation=data.get("ai_operation") or provenance_data.get("ai_operation"),
    )
    if provenance_data.get("ingested_at"):
        provenance.ingested_at = str(provenance_data["ingested_at"])

    nested_extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    extra = {**nested_extra, **{k: v for k, v in data.items() if k not in _RESERVED}}

    try:
        priority = int(data.get("retrieval_priority") or 0)
    except (TypeError, ValueError):
        priority = 0

    item = TextStrataItem(
        id=item_id,
        type=content_type if isinstance(content_type, ContentType) else ContentType.NOTE,
        title=title,
        aliases=_as_list(data.get("aliases")),
        tags=tags,
        related=_as_list(data.get("related")),
        dependencies=_as_list(data.get("dependencies")),
        handling=HandlingMode.coerce(data.get("handling")),
        preservation=PreservationMode.coerce(data.get("preservation")),
        retrieval_priority=priority,
        provenance=provenance,
        body=body,
        extra=extra,
    )
    return item, suggested, fm


def ingest_text(store: TextStrataStore, raw_text: str, *, fallback_id: str | None = None) -> IngestResult:
    store.ensure_dirs()
    item, suggested, fm = build_item(raw_text, fallback_id=fallback_id)
    result = validate(item)

    original_path = store.save_original(item.id, raw_text)
    normalized_path = None
    published = False
    if result.ok:
        normalized_path = store.publish_normalized(item)
        published = True
        activity.write(store.root, "ingest", item_id=item.id, outcome="published", content_type=item.type.value)
    else:
        activity.write(store.root, "ingest", item_id=item.id, outcome="rejected", errors=result.errors)

    return IngestResult(
        item=item,
        validation=result,
        published=published,
        original_path=original_path,
        normalized_path=normalized_path,
        suggested_tags=suggested,
        frontmatter_conflicts=fm.conflicts,
        had_stacked_frontmatter=fm.had_stacked_blocks,
    )


def ingest_file(store: TextStrataStore, path: str | Path) -> IngestResult:
    p = Path(path)
    return ingest_text(store, p.read_text(encoding="utf-8"), fallback_id=p.stem)
