"""Review-queue use cases for web-facing interfaces."""

from __future__ import annotations

import re
from typing import Any, Sequence

from .. import review
from ..store import TextStrataStore


def _body_excerpt(body: str, limit: int = 480) -> str:
    text = re.sub(r"\s+", " ", body).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def review_queue_payload(store: TextStrataStore, items: Sequence[Any]) -> dict[str, Any]:
    """Return pending reviews enriched with current, non-persisted item context."""
    items_by_id = {item.id: item for item in items}
    pending: list[dict[str, Any]] = []
    for queued in review.list_pending(store):
        entry = dict(queued)
        item = items_by_id.get(str(entry.get("item_id", "")))
        entry["item_exists"] = item is not None
        entry["current_tags"] = list(item.tags) if item is not None else []
        entry["body_excerpt"] = _body_excerpt(item.body) if item is not None else ""
        if item is not None:
            entry["item_title"] = item.title
        pending.append(entry)
    return {"pending": pending, "count": len(pending)}
