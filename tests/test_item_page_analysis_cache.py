"""Item pages reuse one corpus analysis until a note changes, with identical results."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import mkdtemp

from textstrata.application.item_detail import build_item_render_context
from textstrata.web import TextStrataWebApp

NOTE = """---
id: {id}
title: {title}
type: note
tags: [{tag}]
---

{body}
"""


class ItemPageAnalysisCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(mkdtemp())
        os.environ["MARKBASE_WORKSPACE"] = str(self.tmp)
        self.app = TextStrataWebApp(workspace_root=self.tmp)
        normalized = self.app.store.normalized_dir
        normalized.mkdir(parents=True, exist_ok=True)
        notes = [
            ("apps.script", "Apps Script editor", "gs", "Drag a file onto the Apps Script editor. See [[chrome.extension]]."),
            ("chrome.extension", "Chrome extension basics", "gs", "A Manifest V3 extension for the Apps Script editor."),
            ("unrelated.note", "Sourdough starter", "food", "Flour, water and patience."),
        ]
        for item_id, title, tag, body in notes:
            (normalized / f"{item_id}.md").write_text(NOTE.format(id=item_id, title=title, tag=tag, body=body), encoding="utf-8")

    def tearDown(self) -> None:
        self.app.close()

    def _context(self, item_id: str, cached: bool):
        item = self.app.item_by_id(item_id)
        corpus = self.app.items()
        kwargs = {"analysis": self.app.corpus_analysis()} if cached else {}
        return build_item_render_context(item, corpus, synonyms=self.app.synonyms(), **kwargs)

    def test_cached_analysis_gives_identical_page_data(self) -> None:
        for item_id in ("apps.script", "chrome.extension", "unrelated.note"):
            fresh, cached = self._context(item_id, False), self._context(item_id, True)
            for field in ("outgoing_links", "incoming_links", "similar_ids", "knowledge_score",
                          "community", "why_related", "known_ids", "item_titles", "item_aliases"):
                self.assertEqual(getattr(fresh, field), getattr(cached, field), f"{item_id}.{field}")

    def test_analysis_is_reused_until_a_note_changes(self) -> None:
        first = self.app.corpus_analysis()
        self.assertIs(self.app.corpus_analysis(), first)
        path = self.app.store.normalized_dir / "unrelated.note.md"
        time.sleep(0.01)
        path.write_text(path.read_text(encoding="utf-8") + "\nSee [[apps.script]].\n", encoding="utf-8")
        second = self.app.corpus_analysis()
        self.assertIsNot(second, first)
        self.assertTrue(any(l.source == "unrelated.note" and l.target == "apps.script" for l in second.all_links))


if __name__ == "__main__":
    unittest.main()
