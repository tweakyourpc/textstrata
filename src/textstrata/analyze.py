"""Gap analysis for the AuggieAgentWiki-modular-fork-modular-fork knowledge base.

Scans the corpus for missing coverage, stale items,
orphaned symptoms, and structural deficiencies.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .models import ContentType, TextStrataItem
from .store import TextStrataStore


def _days_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def analyze(
    items: Sequence[TextStrataItem],
    store: TextStrataStore | None = None,
) -> dict:
    """Return a structured gap analysis report."""
    total = len(items)
    by_type: Counter[str] = Counter()
    missing_fields: dict[str, list[str]] = {}
    no_tags: list[str] = []
    no_resolution: list[str] = []
    stale: list[dict] = []
    low_priority_incidents: list[str] = []
    tag_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    orphaned: list[str] = []

    for item in items:
        by_type[item.type.value] += 1
        tag_counts.update(t.lower() for t in item.tags)
        cat = str(item.extra.get("category", "") or "")
        if cat:
            category_counts[cat] += 1

        if not item.tags:
            no_tags.append(item.id)

        if item.type in (ContentType.INCIDENT, ContentType.KNOWN_ERROR):
            resolution = str(item.extra.get("resolution", "") or "").strip()
            symptom = str(item.extra.get("symptom", "") or "").strip()
            if not resolution:
                no_resolution.append(item.id)
            if item.retrieval_priority < 1:
                low_priority_incidents.append(item.id)
            if symptom and not resolution:
                missing_fields.setdefault(item.id, []).append("resolution")
            for field in ("symptom", "environment", "affected_systems", "steps_to_reproduce"):
                val = item.extra.get(field)
                if not val or (isinstance(val, list) and not val):
                    missing_fields.setdefault(item.id, []).append(field)

        edited_age = _days_since(str(item.extra.get("last_edited_at") or ""))
        if edited_age is not None and edited_age > 180:
            stale.append({"id": item.id, "title": item.title, "days_since_edit": int(edited_age)})

    # Orphaned: items with no incoming or outgoing links and no shared tags
    if total > 1:
        all_ids = {it.id for it in items}
        linked_ids: set[str] = set()
        for item in items:
            linked_ids.update(item.related)
            linked_ids.update(item.dependencies)
        orphaned = sorted(all_ids - linked_ids)

    return {
        "total_items": total,
        "by_type": dict(by_type.most_common()),
        "no_tags": no_tags,
        "no_resolution": no_resolution,
        "low_priority_incidents": low_priority_incidents,
        "orphaned_items": orphaned,
        "stale_items": stale,
        "top_tags": dict(tag_counts.most_common(15)),
        "categories": dict(category_counts.most_common()),
        "missing_fields": missing_fields,
        "summary": {
            "total": total,
            "incident_types": by_type.get("incident", 0) + by_type.get("known_error", 0),
            "unresolved": len(no_resolution),
            "untagged": len(no_tags),
            "orphaned": len(orphaned),
            "stale": len(stale),
        },
    }


def print_report(report: dict) -> None:
    s = report["summary"]
    print(f"{'='*50}")
    print(f"  AuggieAgentWiki-modular-fork-modular-fork — Knowledge Base Analysis")
    print(f"{'='*50}")
    print(f"  Total items:      {s['total']}")
    print(f"  Incident records: {s['incident_types']}")
    print(f"  Unresolved:       {s['unresolved']}")
    print(f"  Untagged:         {s['untagged']}")
    print(f"  Orphaned:         {s['orphaned']}")
    print(f"  Stale (>6mo):     {s['stale']}")
    print()

    print("Content type breakdown:")
    for t, n in report["by_type"].items():
        print(f"  {t:25s} {n}")
    print()

    if report["no_resolution"]:
        print(f"Incidents missing resolution ({len(report['no_resolution'])}):")
        for iid in report["no_resolution"][:10]:
            print(f"  - {iid}")
        if len(report["no_resolution"]) > 10:
            print(f"  ... and {len(report['no_resolution']) - 10} more")
        print()

    if report["orphaned_items"]:
        print(f"Orphaned items (no cross-links) ({len(report['orphaned_items'])}):")
        for iid in report["orphaned_items"][:10]:
            print(f"  - {iid}")
        print()

    if report["stale_items"]:
        print(f"Stale items (>180 days since edit):")
        for s in report["stale_items"][:10]:
            print(f"  - {s['id']} ({s['days_since_edit']} days)")
        print()

    if report["top_tags"]:
        print("Top tags:")
        for tag, n in report["top_tags"].items():
            print(f"  {tag:20s} {n}")
