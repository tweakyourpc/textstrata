"""Task 12 — ``created_at`` must come from the revision filename, not mtime.

``list_revisions`` reports ``datetime.fromtimestamp(path.stat().st_mtime)`` as
``created_at``. The revision filename already carries the authoritative instant,
written by ``_snapshot`` as ``%Y%m%dT%H%M%S%fZ``. Any operation that rewrites
file times -- a backup and restore, an rsync, a container image copy, an
archive extraction -- silently rewrites the entire revision history to whenever
the copy happened.

Parse the stamp from the filename. Fall back to mtime only when the name is
malformed.
"""

import os
import re
import tempfile
import unittest
from datetime import datetime, timezone

from textstrata.ingest import build_item
from textstrata.store import TextStrataStore


STAMP_RE = re.compile(r"^(\d{8}T\d{6}\d{6}Z)-")


def note(body: str) -> str:
    return f"---\nid: note.history\ntitle: History\ntype: note\ntags: [x]\n---\n\n{body}\n"


class RevisionTimestampTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = TextStrataStore(self.root, revision_limit=3)
        # Publish through the normalized path so revisions accumulate without
        # attempting to replace the stored original (Task 8).
        for body in ("one", "two", "three"):
            item, _, _ = build_item(note(body))
            self.store.publish_normalized(item)
        self.revisions = self.store.list_revisions("note.history")

    def revision_paths(self):
        directory = self.store.revision_dir / "note.history"
        return sorted(directory.glob("*.md"))

    def test_created_at_matches_the_stamp_encoded_in_the_filename(self):
        self.assertTrue(self.revisions)
        for revision in self.revisions:
            with self.subTest(name=revision["name"]):
                match = STAMP_RE.match(str(revision["name"]))
                self.assertIsNotNone(match, "revision name has no parseable stamp")
                expected = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S%fZ").replace(
                    tzinfo=timezone.utc
                )
                self.assertEqual(
                    datetime.fromisoformat(str(revision["created_at"])), expected
                )

    def test_rewriting_every_mtime_does_not_change_created_at(self):
        before = [r["created_at"] for r in self.revisions]
        for path in self.revision_paths():
            os.utime(path, (0, 0))
        after = [r["created_at"] for r in self.store.list_revisions("note.history")]
        self.assertEqual(
            after,
            before,
            "a backup/restore that rewrites mtimes rewrote the revision history",
        )

    def test_created_at_is_not_the_epoch_after_an_mtime_rewrite(self):
        for path in self.revision_paths():
            os.utime(path, (0, 0))
        for revision in self.store.list_revisions("note.history"):
            with self.subTest(name=revision["name"]):
                self.assertNotEqual(
                    datetime.fromisoformat(str(revision["created_at"])).year, 1970
                )

    def test_a_malformed_revision_name_falls_back_to_mtime(self):
        directory = self.store.revision_dir / "note.history"
        stray = directory / "not-a-stamp.md"
        stray.write_text("stray\n", encoding="utf-8")
        os.utime(stray, (0, 0))
        entry = next(r for r in self.store.list_revisions("note.history") if r["name"] == "not-a-stamp.md")
        self.assertEqual(datetime.fromisoformat(str(entry["created_at"])).year, 1970)

    def test_revisions_stay_ordered_newest_first(self):
        stamps = [STAMP_RE.match(str(r["name"])).group(1) for r in self.revisions]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_the_reported_size_is_still_the_file_size(self):
        for revision in self.revisions:
            path = self.store.revision_dir / "note.history" / str(revision["name"])
            self.assertEqual(revision["size"], path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
