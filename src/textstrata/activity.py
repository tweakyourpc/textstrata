from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_path(root: Path) -> Path:
    return root / ".fabric" / "activity.jsonl"


def write(root: Path, action: str, item_id: str | None = None, outcome: str = "success", **extra: Any) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "item_id": item_id,
        "outcome": outcome,
    }
    entry.update(extra)
    p = log_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read(root: Path, limit: int | None = None, since: str | None = None, tail: bool = False) -> list[dict[str, Any]]:
    p = log_path(root)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    if tail and limit:
        lines = lines[-limit:]
    entries: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and entry.get("timestamp", "") < since:
            continue
        entries.append(entry)
        if limit and len(entries) >= limit:
            break
    return entries
