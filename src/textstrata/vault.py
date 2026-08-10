"""Deterministic Obsidian vault import and export."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acquisition import AssetStore
from .catalog import Catalog
from .frontmatter import parse, render
from .ingest import ingest_text
from .models import is_valid_id
from .store import TextStrataStore


_WIKI_RE = re.compile(r"\[\[(?P<target>[^\]|#]+)(?P<suffix>(?:#[^\]|]+)?(?:\|[^\]]+)?)\]\]")
_EMBED_RE = re.compile(r"!\[\[(?P<target>[^\]|#]+)\]\]")
_ASSET_RE = re.compile(r"/asset/(?P<asset>[0-9a-f]{64})(?:\?preview=1)?")


@dataclass(frozen=True)
class VaultEntry:
    path: Path
    relative: str
    item_id: str
    title: str
    aliases: tuple[str, ...]


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result or "untitled"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    if value:
        return [part.strip() for part in str(value).split(",") if part.strip()]
    return []


def _unique_id(candidate: str, used: set[str]) -> str:
    base = candidate if is_valid_id(candidate) else _slug(candidate)
    value = base
    index = 2
    while value in used:
        value = f"{base}-{index}"
        index += 1
    used.add(value)
    return value


def _entry_for(path: Path, root: Path, used: set[str]) -> VaultEntry:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse(raw)
    relative = path.relative_to(root).with_suffix("").as_posix()
    data = parsed.data
    title = str(data.get("title") or "").strip()
    if not title:
        heading = re.search(r"^#\s+(.+)$", parsed.body, re.MULTILINE)
        title = heading.group(1).strip() if heading else path.stem
    preferred = str(data.get("id") or "").strip()
    item_id = _unique_id(preferred if is_valid_id(preferred) else relative, used)
    aliases = _as_list(data.get("aliases"))
    for value in (path.stem, relative):
        if value.casefold() not in {alias.casefold() for alias in aliases} and value.casefold() != title.casefold():
            aliases.append(value)
    return VaultEntry(path, relative, item_id, title, tuple(aliases))


def _target_map(entries: list[VaultEntry]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in entries:
        values = {entry.relative, Path(entry.relative).name, entry.path.stem, entry.title, *entry.aliases}
        for value in values:
            key = value.replace("\\", "/").removesuffix(".md").casefold()
            if key in mapping and mapping[key] != entry.item_id:
                ambiguous.add(key)
            else:
                mapping[key] = entry.item_id
    for key in ambiguous:
        mapping.pop(key, None)
    return mapping


def _rewrite_wikilinks(body: str, mapping: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group("target").replace("\\", "/").removesuffix(".md").strip()
        item_id = mapping.get(target.casefold())
        return f"[[{item_id}{match.group('suffix')}]]" if item_id else match.group(0)
    return _WIKI_RE.sub(replace, body)


def _rewrite_embeds(body: str, attachments: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group("target").replace("\\", "/")
        asset = attachments.get(target.casefold()) or attachments.get(Path(target).name.casefold())
        if not asset:
            return match.group(0)
        return f"![{Path(target).stem}]({asset})"
    return _EMBED_RE.sub(replace, body)


def import_obsidian_vault(store: TextStrataStore, vault_path: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Import Markdown and attachments from an Obsidian vault without network access."""
    root = Path(vault_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    markdown_paths = sorted(path for path in root.rglob("*.md") if ".obsidian" not in path.parts)
    used: set[str] = set()
    entries = [_entry_for(path, root, used) for path in markdown_paths]
    mapping = _target_map(entries)
    assets = AssetStore(store.root)
    attachments: dict[str, str] = {}
    attachment_count = 0
    for path in sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() != ".md" and ".obsidian" not in path.parts):
        asset = assets.put(path.read_bytes(), path.name)
        url = f"/asset/{asset.id}"
        attachments[path.relative_to(root).as_posix().casefold()] = url
        attachments[path.name.casefold()] = url
        attachment_count += 1

    imported = skipped = 0
    errors: list[dict[str, str]] = []
    catalog = Catalog(store.root)
    try:
        for entry in entries:
            if store.normalized_path_for_id(entry.item_id) is not None and not overwrite:
                skipped += 1
                continue
            raw = entry.path.read_text(encoding="utf-8", errors="replace")
            parsed = parse(raw)
            data = dict(parsed.data)
            data.update({"id": entry.item_id, "title": entry.title, "aliases": list(entry.aliases), "obsidian_path": entry.relative, "source_kind": "obsidian-vault"})
            body = _rewrite_embeds(_rewrite_wikilinks(parsed.body, mapping), attachments)
            result = ingest_text(store, render(data, body), fallback_id=entry.item_id)
            if not result.published:
                errors.append({"path": entry.relative, "error": "; ".join(result.validation.errors)})
                continue
            catalog.index_item(result.item)
            imported += 1
    finally:
        catalog.close()
    return {"imported": imported, "skipped": skipped, "attachments": attachment_count, "errors": errors}


def export_obsidian_vault(store: TextStrataStore, target_path: str | Path) -> dict[str, Any]:
    """Export normalized TextStrata notes as an Obsidian-compatible Markdown vault."""
    target = Path(target_path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    items = []
    for path in store.normalized_paths():
        from .ingest import build_item
        items.append(build_item(path.read_text(encoding="utf-8"), fallback_id=path.stem)[0])
    items.sort(key=lambda item: item.id)
    mapping = {value.casefold(): item.id for item in items for value in (item.id, item.title, *getattr(item, "aliases", ())) }
    attachment_dir = target / "attachments"
    attachment_dir.mkdir(exist_ok=True)
    copied_assets: set[str] = set()
    written = 0
    for item in items:
        body = _rewrite_wikilinks(item.body, mapping)
        def asset_replace(match: re.Match[str]) -> str:
            asset_id = match.group("asset")
            metadata_path = store.root / "assets" / "metadata" / f"{asset_id}.json"
            if not metadata_path.is_file():
                return match.group(0)
            import json
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            source = store.root / "assets" / metadata["path"]
            filename = Path(str(metadata.get("original_name") or f"{asset_id}.bin")).name
            destination = attachment_dir / filename
            if destination.exists() and filename not in copied_assets:
                destination = attachment_dir / f"{asset_id[:12]}-{filename}"
            if not destination.exists():
                shutil.copyfile(source, destination)
            copied_assets.add(filename)
            return f"attachments/{destination.name}"
        body = _ASSET_RE.sub(asset_replace, body)
        data = {"id": item.id, "title": item.title, "aliases": list(item.aliases), "tags": list(item.tags), "type": item.type.value}
        (target / f"{item.id}.md").write_text(render(data, body), encoding="utf-8")
        written += 1
    return {"exported": written, "attachments": len(copied_assets), "path": str(target)}
