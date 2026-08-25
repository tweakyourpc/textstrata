"""A sequence-valued ``contributor_chain`` must never reach the catalog as a list.

``Provenance.contributor_chain`` is declared ``str`` and lands in a sqlite TEXT
column at ``catalog._upsert``, but nothing coerced it on the way in. Frontmatter
written by an MCP edit can carry the field as a YAML sequence:

    provenance:
      contributor_chain:
      - via_ai

``build_item`` then handed a Python ``list`` straight through, and the bind in
``_upsert`` raised ``sqlite3.ProgrammingError: Error binding parameter 7``.
Because ``TextStrataWebApp.__init__`` rescans on construction, a single item in
this shape aborted server startup and sent the wrapper into a crash loop -- the
whole store made unservable by one malformed field.

These tests pin the coercion at the ``Provenance`` boundary so every
construction path is covered, and assert that a full rescan survives the shape
end to end rather than only that the parse returns a string.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from textstrata.catalog import Catalog
from textstrata.ingest import build_item
from textstrata.models import Provenance, normalize_contributor_chain
from textstrata.store import TextStrataStore

SEQUENCE_NOTE = """---
id: chain.sequence
title: Sequence Chain
type: note
provenance:
  created_via: markbase-mcp
  contributor_chain:
  - via_ai
---

Body.
"""


class NormalizeContributorChain(unittest.TestCase):
    def test_a_sequence_becomes_the_canonical_joined_string(self):
        self.assertEqual(normalize_contributor_chain(["via_script", "human"]), "via_script, human")

    def test_a_single_element_sequence_becomes_a_bare_string(self):
        self.assertEqual(normalize_contributor_chain(["via_ai"]), "via_ai")

    def test_a_tuple_is_treated_like_a_sequence(self):
        self.assertEqual(normalize_contributor_chain(("via_ai",)), "via_ai")

    def test_blank_members_are_dropped_and_members_are_stripped(self):
        self.assertEqual(normalize_contributor_chain(["via_ai", "", "  human  "]), "via_ai, human")

    def test_none_and_empty_become_the_empty_string(self):
        self.assertEqual(normalize_contributor_chain(None), "")
        self.assertEqual(normalize_contributor_chain(""), "")

    def test_an_existing_string_is_preserved_and_the_result_is_idempotent(self):
        once = normalize_contributor_chain("via_script, human")
        self.assertEqual(once, "via_script, human")
        self.assertEqual(normalize_contributor_chain(once), once)


class ProvenanceCoercesOnConstruction(unittest.TestCase):
    def test_the_field_is_a_string_whatever_shape_it_arrives_in(self):
        for declared in (["via_ai"], ("via_ai",), "via_ai"):
            with self.subTest(declared=declared):
                chain = Provenance(contributor_chain=declared).contributor_chain
                self.assertIsInstance(chain, str)
                self.assertEqual(chain, "via_ai")

    def test_build_item_yields_a_string_for_sequence_frontmatter(self):
        item, _suggested, _fm = build_item(SEQUENCE_NOTE, fallback_id="chain.sequence")
        self.assertIsInstance(item.provenance.contributor_chain, str)
        self.assertEqual(item.provenance.contributor_chain, "via_ai")


class RescanSurvivesSequenceFrontmatter(unittest.TestCase):
    """The regression that actually took the server down: indexing, not parsing."""

    def test_an_item_declaring_a_sequence_chain_indexes_without_a_bind_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = TextStrataStore(root)
            item, _suggested, _fm = build_item(SEQUENCE_NOTE, fallback_id="chain.sequence")
            # Written straight to disk, not via publish_normalized, so the
            # malformed sequence shape survives to the rescan under test.
            store.normalized_dir.mkdir(parents=True, exist_ok=True)
            (store.normalized_dir / f"{item.id}.md").write_text(SEQUENCE_NOTE, encoding="utf-8")

            catalog = Catalog(root)
            try:
                indexed = catalog.rescan(store)
            except sqlite3.ProgrammingError as exc:  # pragma: no cover - the bug
                self.fail(f"rescan could not index a sequence-valued chain: {exc}")

            self.assertEqual(indexed, 1)
            stored = catalog.conn.execute(
                "SELECT contributor_chain FROM item_meta WHERE id = ?", (item.id,)
            ).fetchone()[0]
            self.assertEqual(stored, "via_ai")


if __name__ == "__main__":
    unittest.main()
