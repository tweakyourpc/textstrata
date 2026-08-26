"""Report items whose approved policy or tags disagree with what ingest suggested.

The point is to find rules that are consistently wrong. If every ``policy`` item gets
its handling rewritten by hand, the rule that classifies policies is the thing to fix.

Two distinctions matter, because collapsing either one turns the report into noise:

*Overridden* versus *never applied*. ``build_item`` does not apply a suggested policy;
it reads ``handling`` and ``preservation`` from declared frontmatter only, defaulting to
``UNSET`` and ``PRESERVE_EXACT``. So a suggestion differing from an item that declared
nothing means nobody disagreed -- the advice simply was not adopted. Only a *declared*
value that differs is a real override, and only those are listed. The rest are counted.

*Dropped* versus *added* tags. Ingest publishes ``declared + suggested``, so comparing
the two sets whole flags every item that declared any tag of its own. A suggestion was
rejected only when a suggested tag is absent from the final list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import frontmatter
from ..ingest import build_item
from ..models import EditRecord, HandlingMode, TextStrataItem
from ..store import TextStrataStore


@dataclass
class ScanResult:
    """Everything one pass over the normalized store found."""

    mismatches: list[dict] = field(default_factory=list)
    scanned: int = 0
    # A suggestion exists but the item declared no policy of its own, so there was no
    # disagreement to report.
    unapplied: int = 0
    # Files that could not be parsed. Counted rather than raised: one malformed item
    # must not be able to abort the whole report.
    skipped: list[str] = field(default_factory=list)


def _classification_record(item: TextStrataItem) -> EditRecord | None:
    """Recover the ingest-time classification record for an item.

    Nothing rehydrates ``edited_by`` when a file is parsed, so the records survive only
    as raw dicts under ``extra`` and are rebuilt here.
    """
    for entry in item.extra.get("edited_by") or []:
        if not isinstance(entry, dict):
            continue
        record = EditRecord.from_dict(entry)
        if record.suggested_policy is not None:
            return record
    return None


def _author_declared(store: TextStrataStore, item_id: str, normalized: dict) -> set[str]:
    """Return which policy fields the author actually wrote.

    Read from the stored original when it exists: those are the submitted bytes, which
    an invariant guarantees are never rewritten. Normalized frontmatter cannot answer
    this on its own, because ``canonical_frontmatter`` drops an unset ``handling`` but
    always emits ``preservation`` -- its default is a non-empty value, so its presence
    says nothing about whether anyone chose it.

    With no original on disk, ``handling`` presence is still trustworthy for the same
    reason, while ``preservation`` is simply unknowable and is left out.
    """
    original = store.original_dir / f"{item_id}.md"
    if original.exists():
        try:
            data = frontmatter.parse(original.read_text(encoding="utf-8")).data
        except Exception:
            data = {}
        return {key for key in ("handling", "preservation") if key in data}
    return {"handling"} if "handling" in normalized else set()


def _compare(item: TextStrataItem, declared: set[str], record: EditRecord) -> dict | None:
    """Build a mismatch entry, or None when nothing was actually overridden."""
    suggestion = record.suggested_policy
    assert suggestion is not None  # guaranteed by _classification_record

    handling_overridden = (
        "handling" in declared
        and item.handling is not HandlingMode.UNSET
        and item.handling != suggestion.handling
    )
    preservation_overridden = (
        "preservation" in declared and item.preservation != suggestion.preservation
    )
    dropped_tags = [t for t in record.suggested_tags if t not in item.tags]

    if not (handling_overridden or preservation_overridden or dropped_tags):
        return None

    return {
        "item_id": item.id,
        "item_title": item.title,
        "suggested_handling": suggestion.handling.value,
        "suggested_preservation": suggestion.preservation.value,
        "suggested_rationale": suggestion.rationale,
        "actual_handling": item.handling.value,
        "actual_preservation": item.preservation.value,
        "suggested_tags": list(record.suggested_tags),
        "actual_tags": list(item.tags),
        "dropped_tags": dropped_tags,
        "policy_overridden": handling_overridden or preservation_overridden,
        "tags_overridden": bool(dropped_tags),
    }


def scan(root: Path) -> ScanResult:
    """Scan the normalized store for ingest-time overrides."""
    outcome = ScanResult()
    store = TextStrataStore(root)
    for path in store.normalized_paths():
        try:
            raw = path.read_text(encoding="utf-8")
            item, _suggested, _merged = build_item(raw, fallback_id=path.stem)
            normalized = frontmatter.parse(raw).data
        except Exception as exc:  # one bad file must not abort the report
            outcome.skipped.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue

        outcome.scanned += 1
        record = _classification_record(item)
        if record is None:
            continue

        declared = _author_declared(store, item.id, normalized)
        entry = _compare(item, declared, record)
        if entry is not None:
            outcome.mismatches.append(entry)
        elif not declared:
            outcome.unapplied += 1

    outcome.mismatches.sort(key=lambda m: m["item_id"])
    return outcome


def load_misclassifications(root: Path) -> list[dict]:
    """Return just the mismatch entries for the store at ``root``."""
    return scan(root).mismatches


def format_report(mismatches: list[dict], outcome: ScanResult | None = None) -> str:
    """Format misclassifications as a human-readable report."""
    lines: list[str] = []

    if not mismatches:
        lines.append("No misclassifications found.")
    else:
        lines.append(f"Found {len(mismatches)} misclassification(s):")
        for m in mismatches:
            lines.append("")
            lines.append(f"  Item: {m['item_id']}")
            lines.append(f"  Title: {m['item_title']}")
            if m["policy_overridden"]:
                lines.append("    Suggested policy:")
                lines.append(f"      Handling: {m['suggested_handling']}")
                lines.append(f"      Preservation: {m['suggested_preservation']}")
                lines.append(f"      Rationale: {m['suggested_rationale']}")
                lines.append("    Approved policy:")
                lines.append(f"      Handling: {m['actual_handling']}")
                lines.append(f"      Preservation: {m['actual_preservation']}")
            if m["tags_overridden"]:
                lines.append(f"    Suggested tags dropped: {', '.join(m['dropped_tags'])}")
                lines.append(f"    Approved tags: {', '.join(m['actual_tags']) or '(none)'}")

    if outcome is not None:
        lines.append("")
        lines.append(f"Scanned {outcome.scanned} item(s).")
        if outcome.unapplied:
            lines.append(
                f"{outcome.unapplied} item(s) carry a suggestion but declared no policy "
                "of their own, so nothing was overridden."
            )
        if outcome.skipped:
            lines.append(f"Could not read {len(outcome.skipped)} file(s):")
            for note in outcome.skipped:
                lines.append(f"  {note}")

    return "\n".join(lines) + "\n"
