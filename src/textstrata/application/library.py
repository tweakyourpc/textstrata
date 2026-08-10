"""Library and search use cases shared by web-facing interfaces."""

from __future__ import annotations

from html import escape
import re
from pathlib import Path
from typing import Any, Sequence

from ..catalog import Catalog


def item_dt(item: Any, key: str) -> str:
    if key == "edited":
        return str(item.extra.get("last_edited_at") or item.provenance.ingested_at or "")
    return str(item.provenance.ingested_at or "")


def reason_labels(item: Any, q: str) -> list[str]:
    labels: list[str] = []
    terms = [term for term in re.split(r"\s+", q.lower()) if term]
    joined_tags = " ".join(item.tags).lower()
    body = item.body.lower()
    if any(term in item.id.lower() for term in terms):
        labels.append("id match")
    if any(term in item.title.lower() for term in terms):
        labels.append("title match")
    if any(term in joined_tags for term in terms):
        labels.append("tag match")
    if any(term in body for term in terms):
        labels.append("full text")
    return labels[:3]


def retrieval_labels(item: Any, query: str, hit: Any, sort: str) -> list[str]:
    """Explain why a result matched and why the selected sort matters."""
    labels = reason_labels(item, query)
    if sort == "score":
        labels.insert(0, f"graph importance {float(hit.knowledge_score):.3f}")
    elif sort in {"newest", "oldest"} and hit.ingested_at:
        labels.insert(0, f"indexed {str(hit.ingested_at)[:10]}")
    if hit.snippet and "[" in hit.snippet and "full text" not in labels:
        labels.append("matched excerpt")
    return labels[:4]


def render_dashboard(items: Sequence[Any]) -> tuple[str, str]:
    items = list(items)
    recent_imports = sorted(items, key=lambda it: item_dt(it, "ingested"), reverse=True)[:5]
    recent_changes = sorted(items, key=lambda it: item_dt(it, "edited"), reverse=True)[:5]
    needs_curation = [it for it in items if (not it.tags) or not (it.provenance.source_url or it.extra.get("document_date"))][:5]
    tag_counts: dict[str, int] = {}
    for item in items:
        for tag in item.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]

    def links(rows: Sequence[Any]) -> str:
        return "".join(f'<li><a href="/item/{escape(it.id, quote=True)}">{escape(it.title)}</a></li>' for it in rows) or '<li class="empty">None</li>'

    dashboard_html = (
        '<section class="dashboard-grid">'
        '<article class="dash-card"><h2>Recent imports</h2><ul>' + links(recent_imports) + '</ul></article>'
        '<article class="dash-card"><h2>Recent changes</h2><ul>' + links(recent_changes) + '</ul></article>'
        '<article class="dash-card"><h2>Needs curation</h2><ul>' + links(needs_curation) + '</ul></article>'
        '<article class="dash-card"><h2>Top tags</h2><div class="chips">' + (''.join(f'<a class="tag" href="/tag/{escape(tag, quote=True)}">{escape(tag)} ({count})</a>' for tag, count in top_tags) or '<span class="empty">None</span>') + '</div></article>'
        '</section>'
    ) if items else ''
    sidebar_extra = ''
    if recent_changes:
        sidebar_extra = '<section class="sidebar-module"><h2>Recent</h2><nav>' + ''.join(f'<a href="/item/{escape(it.id, quote=True)}"><span>{escape(it.title)}</span><small>{escape(it.type.value.replace("_", " "))}</small></a>' for it in recent_changes[:5]) + '</nav></section>'
    return dashboard_html, sidebar_extra


def search_library(
    root: str | Path,
    items: Sequence[Any],
    query: str,
    *,
    sort: str = "relevance",
    contributor_filter: Sequence[str] = (),
) -> tuple[list[Any], dict[str, list[str]]]:
    catalog = Catalog(root)
    try:
        hits = catalog.search(query, limit=1000, sort=sort, contributor_filter=list(contributor_filter))
    finally:
        catalog.close()
    hit_order = {hit.id: i for i, hit in enumerate(hits)}
    matched_items = sorted(
        [item for item in items if item.id in hit_order],
        key=lambda it: hit_order.get(it.id, 0),
    )
    hit_by_id = {hit.id: hit for hit in hits}
    search_reasons = {item.id: retrieval_labels(item, query, hit_by_id[item.id], sort) for item in matched_items}
    return matched_items, search_reasons


def orphaned_items(items: Sequence[Any], store: Any) -> list[Any]:
    """Return items with no deterministic cross-links to the corpus."""
    from ..analyze import analyze

    items = list(items)
    report = analyze(items, store)
    orphaned_ids = set(report.get("orphaned_items", []))
    return [item for item in items if item.id in orphaned_ids]


def corpus_view(items: Sequence[Any], view: str) -> list[Any]:
    """Return a deterministic, server-backed corpus view."""
    items = list(items)
    if view == "recent":
        return sorted(
            items,
            key=lambda item: item_dt(item, "edited"),
            reverse=True,
        )[:30]
    if view == "needs-curation":
        return [item for item in items if not item.tags or not (item.provenance.source_url or item.extra.get("document_date"))]
    if view == "untagged":
        return [item for item in items if not item.tags]
