"""Library dates come from stored ingest metadata, never from page-load time."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import mkdtemp

from textstrata.application.library import render_dashboard, sort_by_date
from textstrata.catalog import Catalog
from textstrata.web import TextStrataWebApp

ROOT = Path(__file__).resolve().parents[1]

UNDATED = """---
id: {id}
title: {title}
type: note
---

Body of {title}.
"""

LEGACY = """---
id: legacy.markbase.old
title: Old capture
type: reference
extra:
  legacy_markbase_metadata:
    date_ingested: '2026-06-23T05:37:46+00:00'
---

Captured long ago.
"""


class ItemDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(mkdtemp())
        os.environ["MARKBASE_WORKSPACE"] = str(self.tmp)
        self.app = TextStrataWebApp(workspace_root=self.tmp)
        store = self.app.store
        store.normalized_dir.mkdir(parents=True, exist_ok=True)
        for item_id, title, stamp in (
            ("note.older", "Older", "2026-08-01T00:00:00+00:00"),
            ("note.newer", "Newer", "2026-11-22T18:56:54+00:00"),
            ("aaa.middle", "Middle", "2026-10-01T00:00:00+00:00"),
        ):
            (store.normalized_dir / f"{item_id}.md").write_text(
                UNDATED.format(id=item_id, title=title), encoding="utf-8"
            )
            store._write_item_metadata(item_id, {"ingested_at": stamp})

    def tearDown(self) -> None:
        self.app.close()

    def test_items_use_stored_ingest_time(self) -> None:
        dates = {item.id: item.provenance.ingested_at for item in self.app.items()}
        self.assertEqual(dates["note.newer"], "2026-11-22T18:56:54+00:00")
        self.assertEqual(dates["note.older"], "2026-08-01T00:00:00+00:00")

    def test_dashboard_recent_imports_are_newest_first(self) -> None:
        dashboard, _ = render_dashboard(self.app.items())
        recent = dashboard.split("Recent imports", 1)[1].split("</ul>", 1)[0]
        self.assertLess(recent.index("Newer"), recent.index("Middle"))
        self.assertLess(recent.index("Middle"), recent.index("Older"))

    def test_sort_by_date_orders_both_directions(self) -> None:
        items = self.app.items()
        self.assertEqual([i.id for i in sort_by_date(items, "newest")], ["note.newer", "aaa.middle", "note.older"])
        self.assertEqual([i.id for i in sort_by_date(items, "oldest")], ["note.older", "aaa.middle", "note.newer"])

    def test_note_page_item_uses_stored_ingest_time(self) -> None:
        item = self.app.item_by_id("note.newer")
        self.assertEqual(item.provenance.ingested_at, "2026-11-22T18:56:54+00:00")

    def test_mcp_reads_use_stored_ingest_time(self) -> None:
        from textstrata.mcp_server import TextStrataMCP

        mcp = TextStrataMCP(self.tmp)
        try:
            self.assertEqual(mcp._read_item("note.older").provenance.ingested_at, "2026-08-01T00:00:00+00:00")
            dates = {i.id: i.provenance.ingested_at for i in mcp._all_items()}
            self.assertEqual(dates["aaa.middle"], "2026-10-01T00:00:00+00:00")
        finally:
            mcp.catalog.close()

    def test_cli_item_loader_uses_stored_ingest_time(self) -> None:
        from textstrata.__main__ import _load_items

        dates = {i.id: i.provenance.ingested_at for i in _load_items(self.app.store)}
        self.assertEqual(dates["note.newer"], "2026-11-22T18:56:54+00:00")

    def test_repair_script_restores_legacy_capture_date(self) -> None:
        store = self.app.store
        (store.normalized_dir / "legacy.markbase.old.md").write_text(LEGACY, encoding="utf-8")
        store._write_item_metadata("legacy.markbase.old", {"ingested_at": "2026-11-13T23:51:25+00:00"})
        catalog = Catalog(self.tmp)
        catalog.conn.execute(
            "INSERT OR REPLACE INTO item_meta (id, ingested_at) VALUES (?, ?)",
            ("legacy.markbase.old", "2026-11-13T23:51:25+00:00"),
        )
        catalog.conn.commit()
        catalog.close()

        script = ROOT / "scripts" / "repair_legacy_ingested_at.py"
        dry = subprocess.run([sys.executable, str(script), str(self.tmp)], capture_output=True, text=True, check=True)
        self.assertIn("1 item(s) would change", dry.stdout)
        self.assertEqual(store.item_metadata("legacy.markbase.old")["ingested_at"], "2026-11-13T23:51:25+00:00")

        subprocess.run([sys.executable, str(script), str(self.tmp), "--apply"], capture_output=True, text=True, check=True)
        self.assertEqual(store.item_metadata("legacy.markbase.old")["ingested_at"], "2026-06-23T05:37:46+00:00")
        catalog = Catalog(self.tmp)
        row = catalog.conn.execute("SELECT ingested_at FROM item_meta WHERE id = ?", ("legacy.markbase.old",)).fetchone()
        catalog.close()
        self.assertEqual(row[0], "2026-06-23T05:37:46+00:00")


if __name__ == "__main__":
    unittest.main()
