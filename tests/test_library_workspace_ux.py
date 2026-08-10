import tempfile
import unittest

from textstrata.ingest import ingest_text
from textstrata.presentation import PAPER_SKIN, render_library_index
from textstrata.presentation.browser_assets import client_asset_content
from textstrata.store import TextStrataStore


NOTE = """---
id: note.workspace-ux
title: Workspace UX Note
type: reference
tags: [interface, retrieval]
contributor_chain: via_ai, human
provenance:
  ingested_at: 2026-07-20T10:30:00+00:00
  source_url: https://example.test/workspace
---

# Workspace UX Note

Dense rows should expose useful retrieval context without overwhelming readers.
"""


class LibraryWorkspaceUXTests(unittest.TestCase):
    def setUp(self):
        self.store = TextStrataStore(tempfile.mkdtemp())
        self.item = ingest_text(self.store, NOTE).item

    def render(self, **kwargs):
        return render_library_index([self.item], PAPER_SKIN, **kwargs)

    def test_primary_workspace_navigation_is_visible_and_addressable(self):
        html = self.render()

        self.assertIn('<strong>TextStrata</strong>', html)
        self.assertIn('aria-label="Primary"', html)
        self.assertIn('data-workspace-view="library" href="/"', html)
        self.assertIn('data-workspace-view="search" href="/search"', html)
        self.assertIn('data-workspace-view="recent" href="/recent"', html)
        self.assertIn(
            'data-workspace-view="needs-curation" href="/needs-curation"',
            html,
        )
        self.assertIn('data-workspace-view="untagged" href="/untagged"', html)
        for action in ("sync", "review-queue", "trash"):
            self.assertIn(f'data-action="{action}"', html)

    def test_dense_rows_expose_retrieval_and_provenance_signals(self):
        html = self.render(
            search_query="workspace",
            search_reasons={self.item.id: ["title match", "full text"]},
            contributor_filter=["via_ai"],
        )

        self.assertIn('class="entry-main"', html)
        self.assertIn('class="entry-details"', html)
        self.assertIn(f'data-updated="{self.item.provenance.ingested_at}"', html)
        self.assertIn('data-contributors="via_ai,human"', html)
        self.assertIn('data-needs-curation="false"', html)
        self.assertIn('<span class="reason-chip">title match</span>', html)
        self.assertIn('<span class="reason-chip">full text</span>', html)
        self.assertIn('<span class="contributor-chip">AI</span>', html)
        self.assertIn(
            'name="contributor" value="via_ai" checked',
            html,
        )

    def test_saved_views_and_responsive_navigation_have_client_contracts(self):
        html = self.render()
        script = client_asset_content("library", "test", "test")

        self.assertIn('const routeViews = ["search", "recent", "needs-curation", "untagged", "orphaned"]', script)
        self.assertIn('activeView === "needs-curation"', script)
        self.assertIn('activeView === "recent" ? index < 30', script)
        self.assertIn('window.matchMedia("(min-width: 960px)")', script)
        self.assertIn('@media (min-width: 960px)', html)
        self.assertIn('@media (max-width: 700px)', html)
        self.assertIn('.dashboard-grid[hidden] { display: none !important; }', html)
        self.assertIn('.library-bar .btn { white-space: nowrap; }', html)

    def test_default_workspace_skin_is_neutral_and_compact(self):
        self.assertEqual(PAPER_SKIN.page_title, "TextStrata")
        self.assertEqual(PAPER_SKIN.radius, "6px")
        self.assertEqual(PAPER_SKIN.background, "#f4f5f6")
        self.assertEqual(PAPER_SKIN.surface, "#ffffff")


if __name__ == "__main__":
    unittest.main()
