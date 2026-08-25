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

import hashlib
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
    is_valid_id,
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
        provenance._ingested_at_declared = True

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


def _ingest(
    store: TextStrataStore,
    raw_text: str,
    *,
    fallback_id: str | None = None,
    original_bytes: bytes,
    original_sha256: str,
) -> IngestResult:
    store.ensure_dirs()
    item, suggested, fm = build_item(raw_text, fallback_id=fallback_id)
    lock_id = item.id if is_valid_id(item.id) else f"rejected-{original_sha256[:16]}"
    with store.item_lock(lock_id):
        original_path = store._save_original_bytes(
            item.id, original_bytes, original_sha256
        )
        ingest_result = _validate_and_publish(
            store,
            item,
            suggested_tags=suggested,
            frontmatter_result=fm,
            original_path=original_path,
        )
    if ingest_result.published:
        activity.write(store.root, "ingest", item_id=item.id, outcome="published", content_type=item.type.value)
    else:
        activity.write(store.root, "ingest", item_id=item.id, outcome="rejected", errors=ingest_result.validation.errors)
    return ingest_result


def _validate_and_publish(
    store: TextStrataStore,
    item: TextStrataItem,
    *,
    suggested_tags: list[str],
    frontmatter_result: frontmatter.MergedFrontmatter,
    original_path: Path | None = None,
    original_text: str | None = None,
) -> IngestResult:
    """Validate and publish an already-built item without replacing an original."""
    result = validate(item, diagnostics=frontmatter_result.conflicts)
    normalized_path = None
    published = False
    if result.ok:
        with store.item_lock(item.id):
            if original_text is not None:
                original_path = store.save_original(item.id, original_text)
            normalized_path = store._publish_normalized_unlocked(item)
            published = True

    return IngestResult(
        item=item,
        validation=result,
        published=published,
        original_path=original_path,
        normalized_path=normalized_path,
        suggested_tags=suggested_tags,
        frontmatter_conflicts=frontmatter_result.conflicts,
        had_stacked_frontmatter=frontmatter_result.had_stacked_blocks,
    )


def _ingest_update(
    store: TextStrataStore,
    raw_text: str,
    *,
    fallback_id: str | None = None,
) -> IngestResult:
    """Validate and publish an update without touching its stored original."""
    store.ensure_dirs()
    item, suggested, fm = build_item(raw_text, fallback_id=fallback_id)
    ingest_result = _validate_and_publish(
        store,
        item,
        suggested_tags=suggested,
        frontmatter_result=fm,
    )
    if ingest_result.published:
        activity.write(
            store.root,
            "ingest",
            item_id=item.id,
            outcome="published",
            content_type=item.type.value,
        )
    else:
        activity.write(
            store.root,
            "ingest",
            item_id=item.id,
            outcome="rejected",
            errors=ingest_result.validation.errors,
        )
    return ingest_result


def _update_text(
    store: TextStrataStore,
    raw_text: str,
    *,
    fallback_id: str | None = None,
) -> IngestResult:
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return _ingest_update(store, raw_text, fallback_id=fallback_id)


def _update_file(store: TextStrataStore, path: str | Path) -> IngestResult:
    source = Path(path)
    raw_text = source.read_bytes().decode("utf-8")
    return _update_text(store, raw_text, fallback_id=source.stem)


def ingest_text(store: TextStrataStore, raw_text: str, *, fallback_id: str | None = None) -> IngestResult:
    raw_bytes = raw_text.encode("utf-8")
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return _ingest(
        store,
        raw_text,
        fallback_id=fallback_id,
        original_bytes=raw_bytes,
        original_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def ingest_file(store: TextStrataStore, path: str | Path) -> IngestResult:
    p = Path(path)
    raw_bytes = p.read_bytes()
    raw_text = raw_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return _ingest(
        store,
        raw_text,
        fallback_id=p.stem,
        original_bytes=raw_bytes,
        original_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
