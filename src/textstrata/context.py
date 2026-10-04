"""Deterministic, bounded project context assembled from normalized articles."""

from __future__ import annotations

import hashlib
from typing import Any

from .ingest import build_item
from .models import TextStrataItem, is_valid_id
from .store import TextStrataStore

STATUSES = frozenset({"current", "historical", "superseded", "draft"})
MAX_SUMMARY = 600


def context_errors(item: TextStrataItem) -> list[str]:
    """Validate the optional context facet at the normal publication gate."""
    value = item.extra.get("context")
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["context must be a mapping"]
    errors = []
    unknown = set(value) - {"projects", "tier", "status", "summary", "supersedes"}
    if unknown:
        errors.append(f"unknown context fields: {', '.join(sorted(unknown))}")
    projects = value.get("projects")
    if not isinstance(projects, list) or not projects or any(not isinstance(p, str) or not is_valid_id(p) for p in projects):
        errors.append("context.projects must be a nonempty list of project slugs")
    elif len(projects) != len(set(projects)):
        errors.append("context.projects contains duplicates")
    tier = value.get("tier")
    if type(tier) is not int or tier not in (1, 2, 3):
        errors.append("context.tier must be 1, 2, or 3")
    if value.get("status") not in STATUSES:
        errors.append("context.status must be current, historical, superseded, or draft")
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY:
        errors.append(f"context.summary must contain 1 to {MAX_SUMMARY} characters")
    supersedes = value.get("supersedes", [])
    if not isinstance(supersedes, list) or any(not isinstance(i, str) or not is_valid_id(i) or i == item.id for i in supersedes):
        errors.append("context.supersedes must contain other article IDs")
    elif len(supersedes) != len(set(supersedes)):
        errors.append("context.supersedes contains duplicates")
    return errors


def _entry(item: TextStrataItem, *, include_body: bool = False) -> dict[str, Any]:
    facet = item.extra.get("context") or {}
    entry: dict[str, Any] = {
        "id": item.id,
        "title": item.title,
        "summary": facet.get("summary", item.body.strip().split("\n", 1)[0])[:MAX_SUMMARY].strip(),
        "type": item.type.value,
        "related": sorted(set(item.related)),
    }
    if include_body:
        entry["body"] = item.body.strip()[:2400]
    return entry


def get_project_context(store: TextStrataStore, project: str) -> dict[str, Any]:
    """Return stable context tiers without a catalog or an AI dependency.

    Only explicitly designated current records enter tiers 1 and 2. Tier 3
    includes designated records and links from selected records for deeper
    retrieval. The result changes only when normalized Markdown changes.
    """
    project = project.strip().lower()
    if not is_valid_id(project):
        raise ValueError("project must be a stable slug")
    items: dict[str, TextStrataItem] = {}
    raw_by_id: dict[str, bytes] = {}
    for path in sorted(store.normalized_dir.glob("*.md")):
        raw = path.read_bytes()
        item, _, _ = build_item(raw.decode("utf-8"), fallback_id=path.stem)
        errors = context_errors(item)
        if errors:
            raise ValueError(f"invalid context on {item.id}: {', '.join(errors)}")
        items[item.id] = item
        raw_by_id[item.id] = raw
    supersedes = {
        item.id: item.extra["context"].get("supersedes", [])
        for item in items.values() if item.extra.get("context")
    }
    for source, targets in supersedes.items():
        for target in targets:
            if target not in items:
                raise ValueError(f"{source} supersedes missing article {target}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def check_cycle(item_id: str) -> None:
        if item_id in visiting:
            raise ValueError(f"supersession cycle at {item_id}")
        if item_id in visited:
            return
        visiting.add(item_id)
        for target in supersedes.get(item_id, []):
            check_cycle(target)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in sorted(supersedes):
        check_cycle(item_id)
    selected = [
        item for item in items.values()
        if (facet := item.extra.get("context"))
        and facet["status"] == "current"
        and (project in facet["projects"] or "all" in facet["projects"])
    ]
    selected.sort(key=lambda i: (i.extra["context"]["tier"], -i.retrieval_priority, i.id))
    tier1 = [item for item in selected if item.extra["context"]["tier"] == 1][:5]
    tier2 = [item for item in selected if item.extra["context"]["tier"] == 2][:10]
    related = {ref for item in tier1 + tier2 for ref in item.related if ref in items}
    tier3_ids = ({item.id for item in selected if item.extra["context"]["tier"] == 3} | related) - {item.id for item in tier1 + tier2}
    tier3 = [items[item_id] for item_id in sorted(tier3_ids)][:20]
    if not tier1:
        raise ValueError(f"no current tier-1 context for project {project!r}")
    included = tier1 + tier2 + tier3
    digest = hashlib.sha256()
    for item in sorted(included, key=lambda i: i.id):
        digest.update(item.id.encode("utf-8") + b"\0" + raw_by_id[item.id] + b"\0")
    return {
        "project": project,
        "snapshot_sha256": digest.hexdigest(),
        "tier1": [_entry(item, include_body=True) for item in tier1],
        "tier2": [_entry(item) for item in tier2],
        "tier3": [_entry(item) for item in tier3],
    }


def render_project_context(data: dict[str, Any]) -> str:
    lines = [f"# {data['project']} context", f"Snapshot: {data['snapshot_sha256']}"]
    for tier in (1, 2, 3):
        lines.append(f"\n## Tier {tier}")
        for entry in data[f"tier{tier}"]:
            lines.append(f"\n### {entry['title']} (`{entry['id']}`)")
            lines.append(entry["summary"])
            if tier == 1:
                lines.append(entry["body"])
            if entry["related"]:
                lines.append("Related IDs: " + ", ".join(entry["related"]))
    return "\n".join(lines).strip() + "\n"
