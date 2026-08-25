"""Task 0d — normalized output must be a pure function of the input bytes.

``Provenance.ingested_at`` defaults to ``_utcnow()`` and ``canonical_frontmatter()``
serializes provenance wholesale, so the generated timestamp leaks into the published
Markdown and identical input yields different normalized bytes on every ingest.
"""

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from textstrata.catalog import Catalog
from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore


SOURCE = """---
id: note.determinism
title: Determinism
type: note
tags: [determinism]
handling: human_plus_ai
preservation: preserve_exact
---

# Determinism

The same input must normalize to the same bytes.
"""


class NormalizedOutputDeterminismTests(unittest.TestCase):
    def _ingest_into_fresh_store(self, raw: str) -> tuple[bytes, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TextStrataStore(tmp.name)
        result = ingest_text(store, raw)
        self.assertTrue(result.published, result.validation.errors)
        self.assertIsNotNone(result.normalized_path)
        return result.normalized_path.read_bytes(), result.normalized_path

    def test_identical_input_normalizes_to_identical_bytes(self):
        first, first_path = self._ingest_into_fresh_store(SOURCE)
        second, second_path = self._ingest_into_fresh_store(SOURCE)

        # Same item id, so the two stores lay the file down at the same relative path.
        self.assertEqual(first_path.name, second_path.name)
        self.assertEqual(
            first,
            second,
            "identical input produced different normalized bytes across two fresh stores",
        )

    def test_declared_ingested_at_is_preserved_in_normalized_output(self):
        raw = SOURCE.replace(
            "type: note\n",
            "type: note\nprovenance:\n  ingested_at: '2020-01-01T00:00:00+00:00'\n",
        )
        published, path = self._ingest_into_fresh_store(raw)

        # An explicitly declared source timestamp is data, not a generated artifact,
        # so it must survive into the published file.
        self.assertIn(b"2020-01-01T00:00:00+00:00", published)
        self.assertEqual(path.read_bytes(), published)

    def test_generated_ingested_at_remains_available_on_the_item(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TextStrataStore(tmp.name)
        result = ingest_text(store, SOURCE)

        # Dropping the timestamp from canonical Markdown must not drop it from the
        # in-memory item; the catalog and UI sort on it.
        self.assertTrue(result.item.provenance.ingested_at)



class IngestTimeSurvivesACatalogRebuildTests(unittest.TestCase):
    """Verification gap found while checking Task 0d (§6).

    Task 0d requires the generated ``ingested_at`` to be omitted from canonical
    Markdown, and it is. But that Markdown was the only durable record of it.
    ``Catalog.rescan`` rebuilds items from the normalized files, so on a catalog
    without its ``item_meta`` rows -- a deleted ``catalog.db``, a fresh clone of
    a workspace, a restore from a backup that excluded it -- every item is
    re-stamped with the instant of the rebuild.

    The stamps stay distinct, so nothing looks obviously broken. They are simply
    assigned in ``normalized_paths()`` order, which is alphabetical by id. So
    ``list_items()`` and ``search(sort="newest")`` silently switch from ordering
    by ingest time to ordering by filename, and every item claims to have been
    ingested at the moment of the rebuild.

    §5 asks Task 0d to "keep a runtime timestamp for UI/catalog". A timestamp
    that resets on every rebuild does not keep anything. It needs to persist
    outside the canonical Markdown -- `.fabric/` already holds several JSON
    sidecars (``review-queue.json``, ``agent-proposals.json``, the acquisition
    metadata), so the pattern exists.

    These tests pin the behaviour, not the mechanism. The determinism assertions
    are here on purpose: putting the generated timestamp back into the Markdown
    would satisfy the durability half and silently undo Task 0d.
    """

    # Ingested in reverse-alphabetical order on purpose: a rebuild stamps items
    # in normalized_paths() order, so a fixture whose ingest order matches its
    # alphabetical order would keep the right ordering by accident.
    INGEST_ORDER = ("note.zulu", "note.alpha")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = TextStrataStore(self.root)
        for item_id in self.INGEST_ORDER:
            text = f"---\nid: {item_id}\ntitle: {item_id}\ntype: note\ntags: [x]\n---\n\nbody {item_id}\n"
            result = ingest_text(self.store, text)
            self.assertTrue(result.published, result.validation.errors)
            time.sleep(0.02)

    def _rescan(self):
        catalog = Catalog(self.root)
        try:
            catalog.rescan(self.store)
            return [(hit.id, hit.ingested_at) for hit in catalog.list_items()]
        finally:
            catalog.close()

    def _drop_catalog(self):
        for path in sorted(self.root.rglob("catalog.db*")):
            path.unlink()

    def test_ingest_times_survive_losing_the_catalog(self):
        before = self._rescan()
        self._drop_catalog()
        self.assertEqual(
            self._rescan(), before, "every item was re-stamped with the rebuild time"
        )

    def test_newest_first_ordering_survives_losing_the_catalog(self):
        before = [item_id for item_id, _ in self._rescan()]
        self.assertEqual(before, list(reversed(self.INGEST_ORDER)), "fixture is not newest-first")
        self._drop_catalog()
        self.assertEqual(
            [item_id for item_id, _ in self._rescan()],
            before,
            "after a rebuild the catalog orders by filename, not by ingest time",
        )

    def test_rebuilt_stamps_are_not_the_rebuild_time(self):
        self._rescan()
        self._drop_catalog()
        rebuild_started = datetime.now(timezone.utc)
        stamps = [datetime.fromisoformat(stamp) for _, stamp in self._rescan()]
        for stamp in stamps:
            self.assertLess(stamp, rebuild_started, "item claims it was ingested during the rebuild")

    def test_persisting_it_does_not_put_it_back_into_the_markdown(self):
        before = self.store.normalized_path_for_id("note.alpha").read_bytes()
        self._rescan()
        self._drop_catalog()
        self._rescan()
        after = self.store.normalized_path_for_id("note.alpha").read_bytes()
        self.assertEqual(after, before, "rescanning rewrote the normalized file")
        self.assertNotIn(b"ingested_at", after)

    def test_determinism_still_holds_alongside_persistence(self):
        other = TextStrataStore(Path(tempfile.mkdtemp()))
        text = "---\nid: note.alpha\ntitle: note.alpha\ntype: note\ntags: [x]\n---\n\nbody note.alpha\n"
        result = ingest_text(other, text)
        self.assertEqual(
            result.normalized_path.read_bytes(),
            self.store.normalized_path_for_id("note.alpha").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
