"""Human review queue for tag and policy suggestions.

Suggested tags and policies from ingestion sit here until a human
confirms, rejects, or amends them. The queue is a JSON file in the
store root so it survives restarts.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Sequence

from .store import TextStrataStore

_AGENT_PROPOSAL_KINDS = {"note", "tags", "synonym"}


def _queue_path(store: TextStrataStore) -> str:
    return str(store.metadata_dir / "review-queue.json")


def _read(store: TextStrataStore) -> dict[str, Any]:
    try:
        data = json.loads(store.metadata_dir.joinpath("review-queue.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(store: TextStrataStore, data: dict[str, Any]) -> None:
    store._atomic_write(store.metadata_dir / "review-queue.json", json.dumps(data, indent=2, sort_keys=True) + "\n")


def enqueue(
    store: TextStrataStore,
    item_id: str,
    item_title: str,
    suggested_tags: Sequence[str],
    policy_handling: str | None = None,
    policy_preservation: str | None = None,
    policy_reason: str | None = None,
) -> dict[str, Any]:
    data = _read(store)
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "item_id": item_id,
        "item_title": item_title,
        "suggested_tags": list(suggested_tags),
        "policy_handling": policy_handling,
        "policy_preservation": policy_preservation,
        "policy_reason": policy_reason,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    data[item_id] = entry
    _write(store, data)
    return entry


def list_pending(store: TextStrataStore) -> list[dict[str, Any]]:
    data = _read(store)
    pending = [v for v in data.values() if v.get("status") == "pending"]
    pending.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return pending


def confirm_tags(store: TextStrataStore, item_id: str, tags: Sequence[str] | None = None) -> dict[str, Any] | None:
    from .ingest import build_item
    data = _read(store)
    entry = data.get(item_id)
    if entry is None:
        return None
    confirmed_tags = list(tags) if tags is not None else list(entry.get("suggested_tags", []))
    entry["status"] = "confirmed"
    entry["confirmed_tags"] = confirmed_tags
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = store.normalized_path_for_id(item_id)
    if path is not None:
        raw = path.read_text(encoding="utf-8")
        item, _, fm = build_item(raw, fallback_id=item_id)
        existing = {t.lower() for t in item.tags}
        for tag in confirmed_tags:
            if tag.lower() not in existing:
                item.tags.append(tag)
        store.publish_normalized(item)
    _write(store, data)
    return entry


def reject_suggestions(store: TextStrataStore, item_id: str) -> dict[str, Any] | None:
    data = _read(store)
    entry = data.get(item_id)
    if entry is None:
        return None
    entry["status"] = "rejected"
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write(store, data)
    return entry


def dismiss(store: TextStrataStore, item_id: str) -> bool:
    data = _read(store)
    if item_id in data:
        del data[item_id]
        _write(store, data)
        return True
    return False


def enqueue_agent_proposal(
    store: TextStrataStore, kind: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Store an inert agent proposal for later human review.

    Proposal payloads never touch normalized content or vocabulary files.
    A content-derived key makes retries idempotent.
    """
    if kind not in _AGENT_PROPOSAL_KINDS:
        raise ValueError(f"unsupported proposal kind: {kind}")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not canonical or len(canonical.encode("utf-8")) > 1_000_000:
        raise ValueError("proposal payload must be between 1 byte and 1 MiB")
    proposal_id = f"{kind}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
    path = store.metadata_dir / "agent-proposals.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    now = datetime.now(timezone.utc).isoformat()
    entry = data.get(proposal_id, {})
    entry.update({
        "proposal_id": proposal_id,
        "kind": kind,
        "payload": payload,
        "status": entry.get("status", "pending"),
        "updated_at": now,
    })
    entry.setdefault("created_at", now)
    data[proposal_id] = entry
    store._atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return entry


def list_pending_agent_proposals(store: TextStrataStore) -> list[dict[str, Any]]:
    path = store.metadata_dir / "agent-proposals.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    pending = [entry for entry in data.values() if entry.get("status") == "pending"]
    return sorted(pending, key=lambda entry: entry.get("proposal_id", ""))


# --------------------------------------------------------------------------- #
# Vocabulary (synonym) review
# --------------------------------------------------------------------------- #
#
# Corpus-derived synonym proposals live in a separate queue file so they don't
# collide with per-item tag review. Each proposal is keyed "variant->canonical"
# and, when confirmed, is written into the store's synonyms.json so every
# subsequent similarity computation folds the two terms together.

def _syn_queue_path(store: TextStrataStore):
    return store.metadata_dir / "synonym-queue.json"


def _read_syn(store: TextStrataStore) -> dict[str, Any]:
    try:
        data = json.loads(_syn_queue_path(store).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_syn(store: TextStrataStore, data: dict[str, Any]) -> None:
    store._atomic_write(_syn_queue_path(store), json.dumps(data, indent=2, sort_keys=True) + "\n")


def refresh_synonym_proposals(store: TextStrataStore, items: Sequence[Any], *, min_cooccurrence: int = 3) -> list[dict[str, Any]]:
    """Infer synonym proposals from the corpus and enqueue new ones as pending.

    Existing entries keep their status (a rejected proposal stays rejected and
    is not re-suggested). Returns the current list of pending proposals.
    """
    from .vocabulary import infer_synonyms, load_synonyms

    effective = load_synonyms(store.root)
    proposals = infer_synonyms(items, existing=effective, min_cooccurrence=min_cooccurrence)
    data = _read_syn(store)
    now = datetime.now(timezone.utc).isoformat()
    for p in proposals:
        key = f"{p.variant}->{p.canonical}"
        if key in data and data[key].get("status") in {"rejected", "confirmed"}:
            continue
        entry = data.get(key, {})
        entry.update({
            "variant": p.variant,
            "canonical": p.canonical,
            "cooccurrences": p.cooccurrences,
            "reason": p.reason,
            "status": entry.get("status", "pending"),
            "updated_at": now,
        })
        entry.setdefault("created_at", now)
        data[key] = entry
    _write_syn(store, data)
    return list_pending_synonyms(store)


def list_pending_synonyms(store: TextStrataStore) -> list[dict[str, Any]]:
    data = _read_syn(store)
    pending = [v for v in data.values() if v.get("status") == "pending"]
    pending.sort(key=lambda e: (-int(e.get("cooccurrences", 0)), e.get("variant", "")))
    return pending


def confirm_synonym(store: TextStrataStore, variant: str, canonical: str) -> dict[str, Any] | None:
    """Confirm one proposal: write it to synonyms.json and mark it confirmed."""
    from .vocabulary import save_synonyms

    key = f"{variant}->{canonical}"
    data = _read_syn(store)
    entry = data.get(key)
    if entry is None:
        # Allow confirming an ad-hoc mapping the user typed directly.
        entry = {"variant": variant, "canonical": canonical,
                 "cooccurrences": 0, "reason": "manual",
                 "created_at": datetime.now(timezone.utc).isoformat()}
    save_synonyms(store.root, {variant: canonical})
    entry["status"] = "confirmed"
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    data[key] = entry
    _write_syn(store, data)
    return entry


def reject_synonym(store: TextStrataStore, variant: str, canonical: str) -> dict[str, Any] | None:
    key = f"{variant}->{canonical}"
    data = _read_syn(store)
    entry = data.get(key)
    if entry is None:
        return None
    entry["status"] = "rejected"
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_syn(store, data)
    return entry
