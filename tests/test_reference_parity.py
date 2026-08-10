from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from textstrata.catalog import Catalog
from textstrata.ingest import ingest_text
from textstrata.presentation import PAPER_SKIN, RenderContext, render_item_html
from textstrata.store import TextStrataStore
from textstrata.web import TextStrataWebApp, create_handler


ITEM = """---
id: note.parity
title: Reference Parity
type: reference
tags: [accessibility, graph]
related: [note.connected]
---

# Reference Parity

The first paragraph is long enough to support a meaningful spoken selection.

The second paragraph gives playback another independently highlighted block.
"""

CONNECTED = """---
id: note.connected
title: Connected Note
type: reference
tags: [graph]
---

# Connected Note

This note is the target of an explicit relationship.
"""

ORPHAN = """---
id: note.orphan
title: Orphan Note
type: note
tags: []
---

# Orphan Note

This note has no explicit relationship.
"""


class ReferenceParityPresentationTests(unittest.TestCase):
    def test_read_aloud_restores_selection_highlight_and_resume_contracts(self):
        store = TextStrataStore(tempfile.mkdtemp())
        result = ingest_text(store, ITEM)
        context = RenderContext(
            title=result.item.title,
            item=result.item,
            validation_errors=[],
            validation_warnings=[],
            suggested_tags=[],
            raw_markdown=store.normalized_path_for_id(result.item.id).read_text(
                encoding="utf-8"
            ),
        )
        html = render_item_html(context, PAPER_SKIN)

        for marker in (
            'id="ra-select-tip"',
            "Read selection",
            "function raBuildBlocks()",
            "function raPositionsForSelection()",
            "function raStartSelection(startPos,endPos)",
            "function raTryRestore()",
            "textstrata-ra-progress:",
            "Reading selection...",
            'classList.add("ra-reading")',
        ):
            self.assertIn(marker, html)

    def test_item_tags_have_direct_correction_controls(self):
        store = TextStrataStore(tempfile.mkdtemp())
        result = ingest_text(store, ITEM)
        context = RenderContext(
            title=result.item.title,
            item=result.item,
            validation_errors=[],
            validation_warnings=[],
            suggested_tags=[],
        )
        html = render_item_html(context, PAPER_SKIN)

        self.assertEqual(html.count('data-remove-tag="'), 2)
        self.assertIn("/tags/remove", html)
        self.assertEqual(len(re.findall(r'id="([^"]+)"', html)), len(set(re.findall(r'id="([^"]+)"', html))))


class ReferenceParityRouteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = TextStrataWebApp(self.root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.app))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        for note in (ITEM, CONNECTED, ORPHAN):
            ingest_text(self.app.store, note)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
            return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def test_tag_removal_persists_and_reindexes(self):
        status, body = self.request(
            "POST",
            "/api/textstrata/item/note.parity/tags/remove",
            {"tag": "accessibility"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["tags"], ["graph"])
        self.assertEqual(self.app.item_by_id("note.parity").tags, ["graph"])
        status, search = self.request("GET", "/search?q=accessibility")
        self.assertEqual(status, 200)
        self.assertNotIn('id="item-note.parity"', search)
        catalog = Catalog(self.root)
        try:
            self.assertFalse(any(hit.id == "note.parity" for hit in catalog.search("accessibility")))
        finally:
            catalog.close()

    def test_orphaned_view_is_addressable_from_workspace_navigation(self):
        status, body = self.request("GET", "/orphaned")

        self.assertEqual(status, 200)
        self.assertIn("Orphaned items", body)
        self.assertIn("Orphan Note", body)
        self.assertIn('data-workspace-view="orphaned" href="/orphaned"', body)


if __name__ == "__main__":
    unittest.main()
