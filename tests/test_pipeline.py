import tempfile
import unittest
from pathlib import Path

from textstrata import build_links, links_for
from textstrata.catalog import Catalog
from textstrata.ingest import ingest_text
from textstrata.models import ContentType
from textstrata.store import TextStrataStore

SEED = Path(__file__).resolve().parents[1] / "seed" / "textstrata-architecture.md"

NOTE_A = """---
id: note.security
title: Security Practices
type: standard
tags: [security]
handling: human_plus_ai
preservation: preserve_exact
---

# Security Practices

We enforce a CSP and block SSRF. See note.rag for retrieval context.
"""

NOTE_B = """---
id: note.rag
title: Retrieval Notes
type: reference
tags: [rag, security]
related: [note.security]
---

# Retrieval Notes

Retrieval-augmented generation over the textstrata.
"""


class IngestPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)

    def test_seed_note_with_stacked_frontmatter_ingests_cleanly(self):
        res = ingest_text(self.store, SEED.read_text(encoding="utf-8"), fallback_id="seed")
        self.assertTrue(res.published)
        self.assertTrue(res.had_stacked_frontmatter)
        item = res.item
        # Semantic block survived the merge with provenance block.
        self.assertEqual(item.id, "textstrata.architecture")
        self.assertIs(item.type, ContentType.ARCHITECTURE_NOTE)
        self.assertIn("textstrata", item.tags)
        # Provenance block survived too.
        self.assertEqual(item.provenance.created_via, "textstrata-mcp")
        self.assertEqual(item.provenance.authorship, "Codex")
        # Unreserved keys land in extra, not lost.
        self.assertIsNone(item.extra.get("project"))
        self.assertEqual(item.extra.get("version"), "1.0.0")

    def test_original_preserved_separately_from_normalized(self):
        raw = SEED.read_text(encoding="utf-8")
        res = ingest_text(self.store, raw, fallback_id="seed")
        # Original is byte-for-byte, still double-front-matter.
        original = res.original_path.read_text(encoding="utf-8")
        self.assertEqual(original, raw)
        self.assertEqual(original.count("---\n"), raw.count("---\n"))
        # Normalized has a single canonical block.
        normalized = res.normalized_path.read_text(encoding="utf-8")
        self.assertTrue(normalized.startswith("---\n"))
        body_start = normalized.index("\n---\n", 4)
        self.assertNotIn("\n---\n", normalized[body_start + 5:])

    def test_rejected_item_saves_original_but_does_not_publish(self):
        bad = "---\nid: Bad Id\ntitle: Nope\n---\nbody\n"
        res = ingest_text(self.store, bad, fallback_id="bad")
        self.assertFalse(res.published)
        self.assertIsNone(res.normalized_path)
        self.assertIsNotNone(res.original_path)  # original still preserved

    def test_scalar_alias_with_comma_stays_one_value(self):
        res = ingest_text(self.store, "---\nid: note.comma\ntitle: Smith\naliases: Smith, Jr.\n---\nbody\n")
        self.assertTrue(res.published)
        self.assertEqual(res.item.aliases, ["Smith, Jr."])

    def test_cross_linking_signals(self):
        ingest_text(self.store, NOTE_A)
        ingest_text(self.store, NOTE_B)
        # Rebuild TextStrataItems via ingest.build_item for linking.
        from textstrata.ingest import build_item
        objs = [
            build_item(p.read_text(encoding="utf-8"))[0]
            for p in self.store.normalized_paths()
        ]
        links = build_links(objs)
        # note.rag declares `related: [note.security]`, so the explicit
        # reference edge runs rag -> security and outranks the inferred
        # shared-tag edge in that direction.
        rag_links = {(link.target, link.reason) for link in links_for("note.rag", links)}
        self.assertIn(("note.security", "reference"), rag_links)
        # The reverse direction has no declared link, so it falls back to the
        # shared `security` tag.
        sec_links = {(link.target, link.reason) for link in links_for("note.security", links)}
        self.assertIn(("note.rag", "shared_tag"), sec_links)

    def test_wikilinks_become_explicit_edges(self):
        ingest_text(
            self.store,
            """---
id: note.source
title: Source
type: note
---

# Source

See [[note.target]] for the recovery procedure.
""",
        )
        ingest_text(
            self.store,
            """---
id: note.target
title: Target
type: playbook
---

# Target
""",
        )
        from textstrata.ingest import build_item
        objs = [
            build_item(p.read_text(encoding="utf-8"))[0]
            for p in self.store.normalized_paths()
        ]
        links = build_links(objs)
        source_links = {(link.target, link.reason) for link in links_for("note.source", links)}
        self.assertIn(("note.target", "wikilink"), source_links)

    def test_catalog_rescan_and_search(self):
        ingest_text(self.store, NOTE_A)
        ingest_text(self.store, NOTE_B)
        cat = Catalog(Path(self.tmp))
        n = cat.rescan(self.store)
        self.assertEqual(n, 2)
        hits = cat.search("SSRF")
        self.assertTrue(any(h.id == "note.security" for h in hits))
        hits2 = cat.search("retrieval")
        self.assertTrue(any(h.id == "note.rag" for h in hits2))
        cat.close()

    def test_catalog_is_rebuildable_from_scratch(self):
        ingest_text(self.store, NOTE_A)
        cat = Catalog(Path(self.tmp))
        cat.rescan(self.store)
        first = cat.count()
        cat.rescan(self.store)  # idempotent
        self.assertEqual(cat.count(), first)
        cat.close()

    def test_catalog_relevance_fixture_is_deterministic(self):
        for item_id, title, body, priority in [("fixture.exact", "Exact Retrieval", "retrieval graph ranking", 90), ("fixture.title", "Retrieval Operations", "unrelated body", 10), ("fixture.tag", "Operations", "runbook", 50)]:
            ingest_text(self.store, f"---\nid: {item_id}\ntitle: {title}\ntype: reference\ntags: [retrieval]\nretrieval_priority: {priority}\n---\n\n{body}\n")
        cat = Catalog(Path(self.tmp)); cat.rescan(self.store)
        first = [hit.id for hit in cat.search("retrieval", limit=10)]
        self.assertEqual(first, [hit.id for hit in cat.search("retrieval", limit=10)])
        self.assertEqual(first[0], "fixture.exact")
        cat.close()

if __name__ == "__main__":
    unittest.main()
