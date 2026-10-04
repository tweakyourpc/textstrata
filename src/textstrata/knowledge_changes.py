"""Reviewed article changes with content-hash preconditions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import review
from .ingest import _update_text, build_item, ingest_text
from .models import is_valid_id, parse_contributor_chain
from .store import TextStrataStore
from .validate import validate


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def propose_article_change(
    store: TextStrataStore, action: str, content: str, reason: str,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Queue validated content, capturing the current article hash for review."""
    if action not in {"create", "update"}:
        raise ValueError("action must be create or update")
    if not reason.strip():
        raise ValueError("reason is required")
    item, _, parsed = build_item(content)
    result = validate(item, parsed.conflicts)
    if not result.ok:
        raise ValueError("invalid article: " + "; ".join(result.errors))
    if not item.provenance.contributor_chain:
        raise ValueError("contributor_chain is required")
    if "via_ai" in parse_contributor_chain(item.provenance.contributor_chain) and not (item.provenance.ai_vendor and item.provenance.ai_model):
        raise ValueError("AI-authored article needs ai_vendor and exact ai_model")
    sources = sorted(set(source_ids or []))
    for source_id in sources:
        if not is_valid_id(source_id) or store.normalized_path_for_id(source_id) is None:
            raise ValueError(f"missing source article: {source_id}")
    for target in item.related + (item.extra.get("context") or {}).get("supersedes", []):
        if target != item.id and store.normalized_path_for_id(target) is None:
            raise ValueError(f"missing related article: {target}")
    path = store.normalized_path_for_id(item.id)
    if action == "create" and path is not None:
        raise ValueError("article already exists")
    if action == "update" and path is None:
        raise ValueError("article does not exist")
    return review.enqueue_agent_proposal(store, "article_change", {
        "action": action,
        "item_id": item.id,
        "content": content,
        "reason": reason.strip(),
        "source_ids": sources,
        "expected_sha256": _digest(path) if path else None,
    })


def _queue_path(store: TextStrataStore) -> Path:
    return store.metadata_dir / "agent-proposals.json"


def article_change_proposal(store: TextStrataStore, proposal_id: str) -> dict[str, Any]:
    try:
        data = json.loads(_queue_path(store).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("proposal queue unavailable") from exc
    entry = data.get(proposal_id) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("kind") != "article_change":
        raise ValueError("article-change proposal not found")
    return entry


def finish_article_change(store: TextStrataStore, proposal_id: str, *, apply: bool, reviewer: str) -> dict[str, Any]:
    """Apply or reject an explicitly reviewed proposal; stale baselines fail."""
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    entry = article_change_proposal(store, proposal_id)
    if entry.get("status") != "pending":
        raise ValueError("proposal is not pending")
    payload = entry["payload"]
    item_id = payload["item_id"]
    with store.item_lock(item_id):
        if apply:
            path = store.normalized_path_for_id(item_id)
            expected = payload.get("expected_sha256")
            if (None if path is None else _digest(path)) != expected:
                raise ValueError("article changed since proposal; review a new proposal")
            item, _, parsed = build_item(payload["content"])
            if item.id != item_id or not validate(item, parsed.conflicts).ok:
                raise ValueError("proposal content is no longer valid")
            result = ingest_text(store, payload["content"]) if payload["action"] == "create" else _update_text(store, payload["content"], fallback_id=item_id)
            if not result.published:
                raise ValueError("publication rejected: " + "; ".join(result.validation.errors))
        with store.item_lock("agent-proposals"):
            queue = json.loads(_queue_path(store).read_text(encoding="utf-8"))
            current = queue.get(proposal_id)
            if not isinstance(current, dict) or current.get("status") != "pending":
                raise ValueError("proposal status changed")
            current["status"] = "applied" if apply else "rejected"
            current["reviewer"] = reviewer.strip()
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            store._atomic_write(_queue_path(store), json.dumps(queue, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            return current
