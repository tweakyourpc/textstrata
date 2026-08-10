"""Atomic filesystem store with originals, normalized items, cleaned variants, revisions, and trash."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import activity, frontmatter
from .models import TextStrataItem, is_valid_id


class TextStrataStore:
    def __init__(self, workspace_root: str | Path, revision_limit: int | None = None) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.root = self.workspace_root
        self.metadata_dir = self.root / ".fabric"
        self.original_dir = self.root / "original"
        self.normalized_dir = self.root / "normalized"
        self.cleaned_dir = self.root / "cleaned"
        self.revision_dir = self.metadata_dir / "revisions"
        self.trash_dir = self.root / "trash"
        configured = revision_limit if revision_limit is not None else int(os.environ.get("FABRIC_REVISION_LIMIT", "3"))
        self.revision_limit = min(3, max(1, configured))

    def ensure_dirs(self) -> None:
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_metadata()
        for directory in (self.original_dir, self.normalized_dir, self.cleaned_dir, self.revision_dir, self.trash_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _migrate_legacy_metadata(self) -> None:
        """Move known pre-workspace engine state into .fabric without overwrites."""
        names = (
            "activity.jsonl",
            "agent-proposals.json",
            "embeddings.json",
            "textstrata-settings.json",
            "observed-errors.json",
            "review-queue.json",
            "synonym-queue.json",
            "synonyms.json",
        )
        for name in names:
            source = self.root / name
            target = self.metadata_dir / name
            if source.exists() and not target.exists():
                os.replace(source, target)
        for name in ("acquisition", "revisions"):
            source = self.root / name
            target = self.metadata_dir / name
            if source.is_dir() and not target.exists():
                os.replace(source, target)

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _item_path(self, directory: Path, item_id: str) -> Path:
        if not is_valid_id(item_id):
            raise ValueError("invalid TextStrata item ID")
        return directory / f"{item_id}.md"

    def save_original(self, item_id: str, raw_text: str) -> Path:
        if is_valid_id(item_id):
            path = self._item_path(self.original_dir, item_id)
        else:
            digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]
            path = self.original_dir / f"rejected-{digest}.md"
        self._atomic_write(path, raw_text)
        return path

    def _snapshot(self, item_id: str, path: Path) -> Path | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.revision_dir / item_id / f"{stamp}-{digest}.md"
        self._atomic_write(target, text)
        revisions = sorted(target.parent.glob("*.md"), reverse=True)
        for stale in revisions[self.revision_limit:]:
            stale.unlink(missing_ok=True)
        return target

    def publish_normalized(self, item: TextStrataItem) -> Path:
        path = self._item_path(self.normalized_dir, item.id)
        rendered = frontmatter.render(item.canonical_frontmatter(), item.body)
        if path.exists() and path.read_text(encoding="utf-8") != rendered:
            self._snapshot(item.id, path)
        self._atomic_write(path, rendered)
        return path

    def normalized_paths(self) -> list[Path]:
        if not self.normalized_dir.exists():
            return []
        return sorted(self.normalized_dir.glob("*.md"))

    def normalized_path_for_id(self, item_id: str) -> Path | None:
        try:
            path = self._item_path(self.normalized_dir, item_id)
        except ValueError:
            return None
        return path if path.exists() else None

    def publish_cleaned(self, item: TextStrataItem) -> Path:
        path = self._item_path(self.cleaned_dir, item.id)
        rendered = frontmatter.render(item.canonical_frontmatter(), item.cleaned_body)
        self._atomic_write(path, rendered)
        return path

    def cleaned_path_for_id(self, item_id: str) -> Path | None:
        try:
            path = self._item_path(self.cleaned_dir, item_id)
        except ValueError:
            return None
        return path if path.exists() else None

    def cleaned_paths(self) -> list[Path]:
        if not self.cleaned_dir.exists():
            return []
        return sorted(self.cleaned_dir.glob("*.md"))

    def list_revisions(self, item_id: str) -> list[dict[str, object]]:
        if not is_valid_id(item_id):
            raise ValueError("invalid TextStrata item ID")
        directory = self.revision_dir / item_id
        return [
            {"name": path.name, "size": path.stat().st_size, "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
            for path in sorted(directory.glob("*.md"), reverse=True)
        ] if directory.exists() else []

    def restore_revision(self, item_id: str, revision_name: str) -> Path:
        if not is_valid_id(item_id) or Path(revision_name).name != revision_name or not revision_name.endswith(".md"):
            raise ValueError("invalid revision")
        source = self.revision_dir / item_id / revision_name
        target = self._item_path(self.normalized_dir, item_id)
        if not source.is_file() or not target.is_file():
            raise FileNotFoundError(revision_name)
        restored = source.read_text(encoding="utf-8")
        self._snapshot(item_id, target)
        self._atomic_write(target, restored)
        return target

    def trash_item(self, item_id: str) -> dict[str, str]:
        normalized = self._item_path(self.normalized_dir, item_id)
        if not normalized.exists():
            raise FileNotFoundError(item_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        trash_name = f"{stamp}__{item_id}"
        target = self.trash_dir / trash_name
        target.mkdir(parents=True, exist_ok=False)
        os.replace(normalized, target / "normalized.md")
        original = self._item_path(self.original_dir, item_id)
        if original.exists():
            os.replace(original, target / "original.md")
        cleaned = self._item_path(self.cleaned_dir, item_id)
        if cleaned.exists():
            cleaned.rename(target / "cleaned.md")
        self._atomic_write(target / "manifest.json", json.dumps({"item_id": item_id, "deleted_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
        activity.write(self.root, "trash", item_id=item_id, outcome="trashed", trash_name=trash_name)
        return {"item_id": item_id, "trash_name": trash_name}

    def list_trash(self) -> list[dict[str, str]]:
        if not self.trash_dir.exists():
            return []
        items: list[dict[str, str]] = []
        for child in sorted((p for p in self.trash_dir.iterdir() if p.is_dir()), reverse=True):
            try:
                data = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            items.append({"trash_name": child.name, "item_id": str(data.get("item_id") or "unknown"), "deleted_at": str(data.get("deleted_at") or "")})
        return items

    def _trash_child(self, trash_name: str) -> Path:
        if Path(trash_name).name != trash_name or trash_name.startswith("."):
            raise ValueError("invalid trash name")
        path = self.trash_dir / trash_name
        if not path.is_dir():
            raise FileNotFoundError(trash_name)
        return path

    def restore_trash(self, trash_name: str) -> str:
        source = self._trash_child(trash_name)
        data = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        item_id = str(data["item_id"])
        normalized = self._item_path(self.normalized_dir, item_id)
        if normalized.exists():
            raise FileExistsError(item_id)
        normalized.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source / "normalized.md", normalized)
        original_source = source / "original.md"
        if original_source.exists():
            original = self._item_path(self.original_dir, item_id)
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original_source, original)
        (source / "manifest.json").unlink(missing_ok=True)
        source.rmdir()
        activity.write(self.root, "restore", item_id=item_id, outcome="restored", trash_name=trash_name)
        return item_id

    def purge_trash(self, trash_name: str | None = None) -> int:
        if trash_name:
            shutil.rmtree(self._trash_child(trash_name))
            activity.write(self.root, "purge", outcome="purged_single", trash_name=trash_name)
            return 1
        activity.write(self.root, "purge", outcome="purged_all")
        items = [p for p in self.trash_dir.iterdir() if p.is_dir()] if self.trash_dir.exists() else []
        for item in items:
            shutil.rmtree(item)
        return len(items)

    def read_normalized(self, path: Path) -> frontmatter.MergedFrontmatter:
        return frontmatter.parse(path.read_text(encoding="utf-8"))

    def read_normalized_item(self, item_id: str) -> frontmatter.MergedFrontmatter:
        path = self.normalized_path_for_id(item_id)
        if path is None:
            raise FileNotFoundError(item_id)
        return self.read_normalized(path)
