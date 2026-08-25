"""Task 11 — the rename in ``_atomic_write`` must be durable.

``_atomic_write`` writes to a temp file, flushes, ``fsync``s the file, then
``os.replace``s it into position. The file's contents are durable; the directory
entry that names them is not. A crash after the rename can leave the directory
still pointing at the old inode, or at nothing, on filesystems that do not
journal metadata alongside data. The parent directory needs its own fsync.

The same function leaves ``.tmp`` files behind if it dies between ``mkstemp``
and ``os.replace``. Those need sweeping, but only inside directories the store
manages -- never a user's own files.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore


ITEM = "---\nid: note.durable\ntitle: Durable\ntype: note\ntags: [x]\n---\n\nbody\n"


def fresh_store():
    return TextStrataStore(tempfile.mkdtemp())


class ParentDirectoryFsyncTests(unittest.TestCase):
    """fsync is not observable from the filesystem, so watch the calls."""

    def _fsynced_inodes(self, action):
        real_fsync = os.fsync
        seen = []

        def spy(fd):
            try:
                seen.append(os.fstat(fd).st_ino)
            except OSError:
                pass
            return real_fsync(fd)

        with patch("textstrata.store.os.fsync", side_effect=spy):
            action()
        return seen

    def test_the_parent_directory_is_fsynced(self):
        store = fresh_store()
        store.ensure_dirs()
        target = store.normalized_dir / "note.durable.md"
        inodes = self._fsynced_inodes(
            lambda: store._atomic_write(target, ITEM)
        )
        self.assertIn(
            target.parent.stat().st_ino,
            inodes,
            "only the file was fsynced; the rename is not durable",
        )

    def test_the_file_itself_is_still_fsynced(self):
        store = fresh_store()
        store.ensure_dirs()
        target = store.normalized_dir / "note.durable.md"
        inodes = self._fsynced_inodes(lambda: store._atomic_write(target, ITEM))
        self.assertIn(target.stat().st_ino, inodes)

    def test_publishing_through_ingest_fsyncs_the_parent(self):
        store = fresh_store()
        store.ensure_dirs()
        inodes = self._fsynced_inodes(lambda: ingest_text(store, ITEM))
        self.assertIn(store.normalized_dir.stat().st_ino, inodes)


class OrphanedTempSweepTests(unittest.TestCase):
    def setUp(self):
        self.store = fresh_store()
        self.store.ensure_dirs()

    def stray(self, directory: Path, name="orphan.tmp") -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text("half-written\n", encoding="utf-8")
        return path

    def test_orphaned_tmp_files_are_swept_from_managed_directories(self):
        strays = [
            self.stray(self.store.normalized_dir),
            self.stray(self.store.original_dir),
            self.stray(self.store.cleaned_dir),
            self.stray(self.store.revision_dir / "note.durable"),
            self.stray(self.store.trash_dir),
        ]
        self.store.ensure_dirs()
        for path in strays:
            with self.subTest(path=str(path)):
                self.assertFalse(path.exists(), f"orphaned temp file survived: {path}")

    def test_real_content_is_never_swept(self):
        keep = self.store.normalized_dir / "note.durable.md"
        keep.write_text(ITEM, encoding="utf-8")
        also_keep = self.store.normalized_dir / "notes.tmp.md"
        also_keep.write_text(ITEM, encoding="utf-8")
        self.store.ensure_dirs()
        self.assertTrue(keep.exists())
        self.assertTrue(also_keep.exists())

    def test_directories_the_store_does_not_manage_are_left_alone(self):
        outside = self.stray(Path(tempfile.mkdtemp()), "user.tmp")
        sibling = self.stray(self.store.root / "user-data", "user.tmp")
        self.store.ensure_dirs()
        self.assertTrue(outside.exists(), "swept a temp file outside the workspace")
        self.assertTrue(sibling.exists(), "swept a temp file in an unmanaged workspace directory")

    def test_the_sweep_does_not_disturb_a_working_store(self):
        self.stray(self.store.normalized_dir)
        result = ingest_text(self.store, ITEM)
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(result.normalized_path.read_text(encoding="utf-8").count("body"), 1)


if __name__ == "__main__":
    unittest.main()
