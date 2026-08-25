"""Atomic filesystem store with originals, normalized items, cleaned variants, revisions, and trash.

POSIX publication and trash operations are serialized with per-item advisory
locks. On Windows, where ``fcntl.flock`` is unavailable, the lock is only
process-local; callers must enforce a single-writer policy across processes.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import tempfile
import threading
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Iterator

from . import activity, frontmatter
from .models import TextStrataItem, is_valid_id

fcntl: ModuleType | None
try:
    fcntl = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None


_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, object] = {}
_HELD_ITEM_LOCKS = threading.local()


def _revision_limit_from_environment() -> int:
    preferred_name = "TEXTSTRATA_REVISION_LIMIT"
    legacy_name = "FABRIC_REVISION_LIMIT"
    if preferred_name in os.environ:
        name = preferred_name
        raw_value = os.environ[preferred_name]
    elif legacy_name in os.environ:
        name = legacy_name
        raw_value = os.environ[legacy_name]
        warnings.warn(
            f"{legacy_name} is deprecated; use {preferred_name} instead",
            DeprecationWarning,
            stacklevel=2,
        )
    else:
        return 3

    try:
        configured = int(raw_value)
    except ValueError:
        warnings.warn(
            f"Invalid {name} value {raw_value!r}; using 3",
            UserWarning,
            stacklevel=2,
        )
        return 3

    clamped = min(3, max(1, configured))
    if clamped != configured:
        warnings.warn(
            f"{name} value {configured} was clamped to {clamped}",
            UserWarning,
            stacklevel=2,
        )
    return clamped


def _revision_created_at(path: Path) -> str:
    stamp = path.name.split("-", 1)[0]
    try:
        created_at = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        created_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return created_at.isoformat()


class TextStrataStore:
    def __init__(self, workspace_root: str | Path, revision_limit: int | None = None) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.root = self.workspace_root
        self.metadata_dir = self.root / ".fabric"
        self.original_dir = self.root / "original"
        self.normalized_dir = self.root / "normalized"
        self.cleaned_dir = self.root / "cleaned"
        self.revision_dir = self.metadata_dir / "revisions"
        self.item_metadata_dir = self.metadata_dir / "item-metadata"
        self.lock_dir = self.metadata_dir / "locks"
        self.trash_dir = self.root / "trash"
        if revision_limit is None:
            self.revision_limit = _revision_limit_from_environment()
        else:
            self.revision_limit = min(3, max(1, revision_limit))

    def ensure_dirs(self) -> None:
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_metadata()
        for directory in (self.original_dir, self.normalized_dir, self.cleaned_dir, self.revision_dir, self.item_metadata_dir, self.trash_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._sweep_orphaned_temp_files()

    def _sweep_orphaned_temp_files(self) -> None:
        managed = (
            self.original_dir,
            self.normalized_dir,
            self.cleaned_dir,
            self.revision_dir,
            self.item_metadata_dir,
            self.trash_dir,
        )
        for directory in managed:
            for path in directory.rglob("*.tmp"):
                if path.is_file():
                    path.unlink(missing_ok=True)

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
            self._fsync_directory(path.parent)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _atomic_write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            self._fsync_directory(path.parent)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _fsync_directory(self, directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _item_path(self, directory: Path, item_id: str) -> Path:
        if not is_valid_id(item_id):
            raise ValueError("invalid TextStrata item ID")
        return directory / f"{item_id}.md"

    @contextmanager
    def item_lock(self, item_id: str) -> Iterator[None]:
        """Hold the per-item publication lock, re-entrantly in this thread."""
        if not is_valid_id(item_id):
            raise ValueError("invalid TextStrata item ID")
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.lock_dir / f"{item_id}.lock"
        key = str(lock_path)
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())

        process_lock.acquire()  # type: ignore[attr-defined]
        held = getattr(_HELD_ITEM_LOCKS, "depths", None)
        if held is None:
            held = {}
            _HELD_ITEM_LOCKS.depths = held
        depth = held.get(key, 0)
        if depth:
            held[key] = depth + 1
            try:
                yield
            finally:
                held[key] -= 1
                process_lock.release()  # type: ignore[attr-defined]
            return

        handle = None
        try:
            handle = lock_path.open("a+b")
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            held[key] = 1
            yield
        finally:
            held.pop(key, None)
            if handle is not None:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            process_lock.release()  # type: ignore[attr-defined]

    def _item_metadata_path(self, item_id: str) -> Path:
        if not is_valid_id(item_id):
            raise ValueError("invalid TextStrata item ID")
        return self.item_metadata_dir / f"{item_id}.json"

    def item_metadata(self, item_id: str) -> dict[str, object]:
        path = self._item_metadata_path(item_id)
        if not path.exists():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"invalid item metadata for {item_id}")
        return loaded

    def _write_item_metadata(self, item_id: str, metadata: dict[str, object]) -> None:
        payload = {**metadata, "item_id": item_id}
        self._atomic_write(
            self._item_metadata_path(item_id),
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    def _persist_ingested_at(self, item: TextStrataItem) -> None:
        metadata = self.item_metadata(item.id)
        if item.provenance._ingested_at_declared or not metadata.get("ingested_at"):
            metadata["ingested_at"] = item.provenance.ingested_at
            self._write_item_metadata(item.id, metadata)

    def hydrate_item_metadata(self, item: TextStrataItem, source_path: Path) -> None:
        metadata = self.item_metadata(item.id)
        ingested_at = metadata.get("ingested_at")
        if not ingested_at:
            ingested_at = datetime.fromtimestamp(
                source_path.stat().st_mtime, timezone.utc
            ).isoformat()
            metadata["ingested_at"] = ingested_at
            self._write_item_metadata(item.id, metadata)
        item.provenance.ingested_at = str(ingested_at)

    def _save_original_bytes(
        self, item_id: str, raw_bytes: bytes, original_sha256: str
    ) -> Path:
        if is_valid_id(item_id):
            path = self._item_path(self.original_dir, item_id)
        else:
            digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
            path = self.original_dir / f"rejected-{digest}.md"
        if path.exists():
            if path.read_bytes() != raw_bytes:
                raise FileExistsError(path)
            return path
        self._atomic_write_bytes(path, raw_bytes)
        if is_valid_id(item_id):
            metadata = self.item_metadata(item_id)
            metadata["original_sha256"] = original_sha256
            self._write_item_metadata(item_id, metadata)
        return path

    def save_original_bytes(self, item_id: str, raw_bytes: bytes) -> Path:
        return self._save_original_bytes(
            item_id, raw_bytes, hashlib.sha256(raw_bytes).hexdigest()
        )

    def save_original(self, item_id: str, raw_text: str) -> Path:
        """Lossy UTF-8 text wrapper for callers that no longer have source bytes."""
        return self.save_original_bytes(item_id, raw_text.encode("utf-8"))

    def verify_original(self, item_id: str) -> dict[str, object]:
        metadata = self.item_metadata(item_id)
        expected = metadata.get("original_sha256")
        if not expected:
            raise FileNotFoundError(item_id)
        path = self._item_path(self.original_dir, item_id)
        actual = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
        return {
            "item_id": item_id,
            "path": str(path),
            "expected_sha256": str(expected),
            "actual_sha256": actual,
            "matches": actual == expected,
        }

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

    def _publish_normalized_unlocked(self, item: TextStrataItem) -> Path:
        path = self._item_path(self.normalized_dir, item.id)
        rendered = frontmatter.render(item.canonical_frontmatter(), item.body)
        if path.exists() and path.read_text(encoding="utf-8") != rendered:
            self._snapshot(item.id, path)
        self._atomic_write(path, rendered)
        self._persist_ingested_at(item)
        return path

    def publish_normalized(self, item: TextStrataItem) -> Path:
        with self.item_lock(item.id):
            return self._publish_normalized_unlocked(item)

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
            {"name": path.name, "size": path.stat().st_size, "created_at": _revision_created_at(path)}
            for path in sorted(directory.glob("*.md"), reverse=True)
        ] if directory.exists() else []

    def orphaned_revision_dirs(self) -> list[str]:
        """Report revision directories whose normalized item no longer exists."""
        if not self.revision_dir.exists():
            return []
        return sorted(
            path.name
            for path in self.revision_dir.iterdir()
            if path.is_dir() and self.normalized_path_for_id(path.name) is None
        )

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
        with self.item_lock(item_id):
            return self._trash_item_unlocked(item_id)

    def _trash_item_unlocked(self, item_id: str) -> dict[str, str]:
        normalized = self._item_path(self.normalized_dir, item_id)
        if not normalized.exists():
            raise FileNotFoundError(item_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        trash_name = f"{stamp}__{item_id}"
        target = self.trash_dir / trash_name
        target.mkdir(parents=True, exist_ok=False)
        original = self._item_path(self.original_dir, item_id)
        cleaned = self._item_path(self.cleaned_dir, item_id)
        moves = [(normalized, target / "normalized.md")]
        if original.exists():
            moves.append((original, target / "original.md"))
        if cleaned.exists():
            moves.append((cleaned, target / "cleaned.md"))
        revisions = self.revision_dir / item_id
        if revisions.exists():
            moves.append((revisions, target / "revisions"))
        entries = {
            destination.name: str(source.relative_to(self.root))
            for source, destination in moves
        }
        manifest = target / "manifest.json"
        moved: list[tuple[Path, Path]] = []
        try:
            self._atomic_write(
                manifest,
                json.dumps(
                    {
                        "item_id": item_id,
                        "deleted_at": datetime.now(timezone.utc).isoformat(),
                        "entries": entries,
                    },
                    indent=2,
                )
                + "\n",
            )
            for source, destination in moves:
                os.replace(source, destination)
                moved.append((source, destination))
        except Exception:
            for source, destination in reversed(moved):
                os.replace(destination, source)
            manifest.unlink(missing_ok=True)
            target.rmdir()
            raise
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
        try:
            data = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid manifest for trash entry {trash_name}") from exc
        item_id = data.get("item_id")
        if not isinstance(item_id, str) or not is_valid_id(item_id):
            raise ValueError(f"invalid manifest for trash entry {trash_name}: missing item_id")
        with self.item_lock(item_id):
            return self._restore_trash_unlocked(trash_name)

    def _restore_trash_unlocked(self, trash_name: str) -> str:
        source = self._trash_child(trash_name)
        manifest = source / "manifest.json"
        try:
            manifest_bytes = manifest.read_bytes()
            data = json.loads(manifest_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid manifest for trash entry {trash_name}") from exc
        item_id = data.get("item_id")
        if not isinstance(item_id, str) or not is_valid_id(item_id):
            raise ValueError(f"invalid manifest for trash entry {trash_name}: missing item_id")
        self._item_path(self.normalized_dir, item_id)

        raw_entries = data.get("entries")
        if isinstance(raw_entries, dict):
            entries = raw_entries
        else:
            entries = {
                name: str(destination.relative_to(self.root))
                for name, destination in (
                    ("normalized.md", self._item_path(self.normalized_dir, item_id)),
                    ("original.md", self._item_path(self.original_dir, item_id)),
                    ("cleaned.md", self._item_path(self.cleaned_dir, item_id)),
                )
                if (source / name).exists()
            }

        actual_entries = {child.name for child in source.iterdir() if child != manifest}
        if actual_entries != set(entries):
            raise ValueError(f"invalid manifest for trash entry {trash_name}: entries mismatch")

        moves: list[tuple[Path, Path]] = []
        for name, relative_destination in entries.items():
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not isinstance(relative_destination, str)
            ):
                raise ValueError(f"invalid manifest for trash entry {trash_name}: invalid entry")
            destination = (self.root / relative_destination).resolve()
            if self.root != destination and self.root not in destination.parents:
                raise ValueError(f"invalid manifest for trash entry {trash_name}: invalid destination")
            if destination.exists():
                raise FileExistsError(item_id)
            moves.append((source / name, destination))

        moved: list[tuple[Path, Path]] = []
        try:
            for entry_source, destination in moves:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(entry_source, destination)
                moved.append((entry_source, destination))
            manifest.unlink()
            source.rmdir()
        except Exception:
            for entry_source, destination in reversed(moved):
                os.replace(destination, entry_source)
            if not manifest.exists():
                self._atomic_write_bytes(manifest, manifest_bytes)
            raise
        activity.write(self.root, "restore", item_id=item_id, outcome="restored", trash_name=trash_name)
        return item_id

    def purge_trash(self, trash_name: str | None = None) -> int:
        if trash_name:
            shutil.rmtree(self._trash_child(trash_name))
            activity.write(self.root, "purge", outcome="purged_single", trash_name=trash_name)
            return 1
        items = [p for p in self.trash_dir.iterdir() if p.is_dir()] if self.trash_dir.exists() else []
        for item in items:
            shutil.rmtree(item)
        activity.write(self.root, "purge", outcome="purged_all")
        return len(items)

    def read_normalized(self, path: Path) -> frontmatter.MergedFrontmatter:
        return frontmatter.parse(path.read_text(encoding="utf-8"))

    def read_normalized_item(self, item_id: str) -> frontmatter.MergedFrontmatter:
        path = self.normalized_path_for_id(item_id)
        if path is None:
            raise FileNotFoundError(item_id)
        return self.read_normalized(path)
