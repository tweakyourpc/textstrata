"""Safe item mutations shared by HTTP and future CLI surfaces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import activity, frontmatter
from ..catalog import Catalog
from ..models import is_valid_id
from ..ingest import build_item
from ..validate import validate
from ..store import TextStrataStore


_WIKILINK_TARGET_RE = re.compile(r"(\[\[)([^\]|#]+)(?P<suffix>(?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]")


def _rewrite_body(body: str, old_id: str, new_id: str) -> str:
    def wiki(match: re.Match[str]) -> str:
        if match.group(2).strip().casefold() != old_id.casefold():
            return match.group(0)
        return f"{match.group(1)}{new_id}{match.group('suffix')}]]"

    body = _WIKILINK_TARGET_RE.sub(wiki, body)
    return re.sub(
        rf"(/item/){re.escape(old_id)}(?=[\"'/?#)])",
        rf"\g<1>{new_id}",
        body,
        flags=re.IGNORECASE,
    )


def _replace_id_values(value: Any, old_id: str, new_id: str) -> Any:
    if isinstance(value, list):
        return [new_id if str(entry) == old_id else entry for entry in value]
    if isinstance(value, str) and value == old_id:
        return new_id
    return value


def rename_item(store: TextStrataStore, item_id: str, new_id: str) -> dict[str, Any]:
    """Rename an item and update normalized inbound references atomically enough for the local store.

    Originals are moved without rewriting; normalized files are canonicalized so
    their frontmatter and wiki links point at the new identity.
    """
    if not is_valid_id(item_id) or not is_valid_id(new_id):
        raise ValueError("item IDs must contain lowercase letters, numbers, dots, underscores, or hyphens")
    if item_id == new_id:
        return {"renamed": False, "item_id": item_id, "updated_references": 0}
    source = store.normalized_path_for_id(item_id)
    if source is None:
        raise FileNotFoundError(item_id)
    if store.normalized_path_for_id(new_id) is not None:
        raise FileExistsError(new_id)

    paths = store.normalized_paths()
    changed = 0
    rendered: dict[Path, str] = {}
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        parsed = frontmatter.parse(raw)
        data = dict(parsed.data)
        before = (dict(data), parsed.body)
        if path == source or str(data.get("id") or path.stem) == item_id:
            data["id"] = new_id
        for key in ("related", "dependencies"):
            if key in data:
                data[key] = _replace_id_values(data[key], item_id, new_id)
        body = _rewrite_body(parsed.body, item_id, new_id)
        if (data, body) != before:
            changed += 1
            rendered[path] = frontmatter.render(data, body)

    new_source = store.normalized_dir / f"{new_id}.md"
    for path, value in rendered.items():
        target = new_source if path == source else path
        if path == source:
            store._snapshot(item_id, path)
        store._atomic_write(target, value)
    if source != new_source and source.exists():
        source.unlink()

    old_original = store.original_dir / f"{item_id}.md"
    new_original = store.original_dir / f"{new_id}.md"
    if old_original.exists() and not new_original.exists():
        old_original.rename(new_original)
    old_cleaned = store.cleaned_dir / f"{item_id}.md"
    new_cleaned = store.cleaned_dir / f"{new_id}.md"
    if old_cleaned.exists() and not new_cleaned.exists():
        old_cleaned.rename(new_cleaned)

    catalog = Catalog(store.root)
    try:
        catalog.rescan(store)
    finally:
        catalog.close()
    activity.write(store.root, "rename", item_id=item_id, new_item_id=new_id, updated_references=changed)
    return {"renamed": True, "item_id": new_id, "previous_item_id": item_id, "updated_references": changed}


def update_aliases(store: TextStrataStore, item_id: str, aliases: list[str]) -> dict[str, Any]:
    """Replace an item's aliases while preserving its body and provenance."""
    path = store.normalized_path_for_id(item_id)
    if path is None:
        raise FileNotFoundError(item_id)
    item, _suggested, _fm = build_item(path.read_text(encoding="utf-8"), fallback_id=item_id)
    clean: list[str] = []
    for alias in aliases:
        value = " ".join(str(alias).split()).strip()
        if value and value.casefold() not in {item.title.casefold(), item.id.casefold()} and value.casefold() not in {entry.casefold() for entry in clean}:
            clean.append(value)
    item.aliases = clean
    result = validate(item)
    if not result.ok:
        raise ValueError("; ".join(result.errors) or "Alias update failed validation")
    store.publish_normalized(item)
    catalog = Catalog(store.root)
    try:
        catalog.index_item(item)
    finally:
        catalog.close()
    activity.write(store.root, "aliases", item_id=item_id, aliases=clean, outcome="updated")
    return {"saved": True, "item_id": item_id, "aliases": clean}
