"""TEXTSTRATA_MIRROR_SINCE stops new Library copies of older items only."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from textstrata.google_bridge import ingest_bridge, mirror_bridge
from textstrata.store import TextStrataStore

from test_google_bridge import FakeBridge


class MirrorSinceTests(unittest.TestCase):
    def _store_with_item(self, temp: str, ingested_at: str) -> TextStrataStore:
        store = TextStrataStore(temp)
        ingest_bridge(store, FakeBridge())
        store._write_item_metadata("ts-example-001", {"ingested_at": ingested_at})
        return store

    def test_old_item_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self._store_with_item(temp, "2026-08-15T00:00:00+00:00")
            bridge = FakeBridge()
            with patch.dict("os.environ", {"TEXTSTRATA_MIRROR_SINCE": "2026-09-01"}):
                self.assertEqual(mirror_bridge(store, bridge), [])
            self.assertNotIn("mirror_upsert", [action for action, _ in bridge.calls])

    def test_recent_item_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self._store_with_item(temp, "2026-09-01T00:00:00+00:00")
            with patch.dict("os.environ", {"TEXTSTRATA_MIRROR_SINCE": "2026-09-01"}):
                self.assertEqual(mirror_bridge(store, FakeBridge())[0]["action"], "CREATE")

    def test_already_mirrored_old_item_keeps_updating(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self._store_with_item(temp, "2026-07-01T00:00:00+00:00")
            bridge = FakeBridge()
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "CREATE")
            path = store.normalized_paths()[0]
            path.write_text(path.read_text() + "\nChanged\n")
            with patch.dict("os.environ", {"TEXTSTRATA_MIRROR_SINCE": "2026-09-01"}):
                self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "UPDATE")


if __name__ == "__main__":
    unittest.main()
