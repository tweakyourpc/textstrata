"""Task 15 - a later block may not establish a protected key.

Task 1 fixed what happens when two blocks both declare a protected key: the
first wins and a conflict is recorded. It said nothing about a protected key
that only ONE block declares, and that block is not the first. ``_merge_into``
takes the ``key not in acc`` branch and simply adds it, with no conflict and no
warning, so document body text absorbed as a later block can supply an id, a
title or a type the document never declared.

This is guarantee 2, not classification. It does not depend on deciding whether
a later block is prose or deliberate front matter, which is why it sidesteps the
comment-versus-heading ambiguity left open by Task 4. Nothing in this file
inspects a block's shape.

THE RULE, as adopted. The protected set is fixed by the FIRST BLOCK THAT
DECLARES ANY PROTECTED KEY, which this file calls the anchor block. Blocks after
the anchor may add unprotected keys only. Blocks BEFORE the anchor are ordinary
front matter: a provenance block stacked above a semantic block establishes no
protected key, so the semantic block is the anchor and the document is valid.
That shape is the project's own seed corpus and its motivating case, and it is
pinned here as accepted rather than rejected.

An earlier wording fixed the set at block 1 unconditionally. It rejected the
seed, and was amended.
"""

import tempfile
import unittest
from pathlib import Path

from test_frontmatter_merge_types import establishment_message
from textstrata import frontmatter
from textstrata.ingest import build_item, ingest_text
from textstrata.store import TextStrataStore
from textstrata.validate import validate


PROTECTED = ("id", "type", "title", "handling", "preservation", "dependencies", "related")

# Values that are individually valid, so nothing else can be blamed for a
# rejection. Block 1 always declares at least one protected key, so both
# readings of the ruling agree these later declarations are out of bounds.
ESTABLISHED_LATE = {
    "id": ("---\ntitle: T\ntype: note\ntags: [x]\n---\n---\nid: note.hijacked\n---\n\nbody\n", "note.hijacked"),
    "title": ("---\nid: note.x\ntype: note\ntags: [x]\n---\n---\ntitle: The Great Escape\n---\n\nbody\n", "The Great Escape"),
    "type": ("---\nid: note.x\ntitle: T\ntags: [x]\n---\n---\ntype: incident\n---\n\nbody\n", "incident"),
    "handling": ("---\nid: note.x\ntitle: T\ntype: note\ntags: [x]\n---\n---\nhandling: ai_only\n---\n\nbody\n", "ai_only"),
    "preservation": ("---\nid: note.x\ntitle: T\ntype: note\ntags: [x]\n---\n---\npreservation: rewrite_allowed\n---\n\nbody\n", "rewrite_allowed"),
    "dependencies": ("---\nid: note.x\ntitle: T\ntype: note\ntags: [x]\n---\n---\ndependencies: [note.dep]\n---\n\nbody\n", "note.dep"),
    "related": ("---\nid: note.x\ntitle: T\ntype: note\ntags: [x]\n---\n---\nrelated: [note.rel]\n---\n\nbody\n", "note.rel"),
}


def fresh_store():
    return TextStrataStore(tempfile.mkdtemp())


class ALaterBlockCannotEstablishAProtectedKeyTests(unittest.TestCase):
    def test_the_key_does_not_reach_the_merged_data(self):
        for key, (text, value) in ESTABLISHED_LATE.items():
            with self.subTest(key=key):
                fm = frontmatter.parse(text)
                self.assertEqual(fm.block_count, 2, "fixture is not two blocks")
                self.assertNotIn(
                    value, str(fm.data.get(key)),
                    f"{key} was established by block 2",
                )

    def test_the_pinned_establishment_message_is_emitted(self):
        # §5 pins this form; establishment_message() is its single definition.
        for key, (text, _value) in ESTABLISHED_LATE.items():
            with self.subTest(key=key):
                fm = frontmatter.parse(text)
                self.assertEqual(
                    fm.conflicts,
                    [establishment_message(key, 2, anchor_block=1)],
                    f"{key}: establishment diagnostic is missing or off-format",
                )

    def test_it_is_an_error_not_a_warning(self):
        for key, (text, _value) in ESTABLISHED_LATE.items():
            with self.subTest(key=key):
                item, _suggested, fm = build_item(text)
                result = validate(item, diagnostics=fm.conflicts)
                self.assertFalse(result.ok, f"{key}: publishable anyway")
                self.assertTrue(result.errors)

    def test_the_item_does_not_publish(self):
        for key, (text, _value) in ESTABLISHED_LATE.items():
            with self.subTest(key=key):
                result = ingest_text(fresh_store(), text)
                self.assertFalse(result.published, f"{key}: published with a late-established key")

    def test_the_document_keeps_the_identity_it_declared(self):
        text, _value = ESTABLISHED_LATE["title"]
        item, _suggested, _fm = build_item(text)
        self.assertEqual(item.id, "note.x")
        self.assertNotEqual(item.title, "The Great Escape")


class UnprotectedKeysAreStillWelcomeTests(unittest.TestCase):
    """The rule must not turn stacked front matter into a single-block format."""

    BASE = "---\nid: note.x\ntitle: T\ntype: note\ntags: [a]\n---\n"

    def test_a_later_block_may_add_an_unprotected_key(self):
        fm = frontmatter.parse(self.BASE + "---\ncreated_via: textstrata-mcp\n---\n\nbody\n")
        self.assertEqual(fm.data["created_via"], "textstrata-mcp")
        self.assertEqual(fm.conflicts, [])

    def test_a_later_block_adding_an_unprotected_key_still_publishes(self):
        result = ingest_text(
            fresh_store(), self.BASE + "---\ncreated_via: textstrata-mcp\n---\n\nbody\n"
        )
        self.assertTrue(result.published, result.validation.errors)

    def test_unprotected_lists_still_union_across_blocks(self):
        fm = frontmatter.parse(self.BASE + "---\ntags: [b, c]\n---\n\nbody\n")
        self.assertEqual(fm.data["tags"], ["a", "b", "c"])
        self.assertEqual(fm.conflicts, [])

    def test_an_unprotected_key_established_late_is_not_a_conflict(self):
        fm = frontmatter.parse(self.BASE + "---\nauthorship: Codex\nsource_url: https://example.com\n---\n\nbody\n")
        self.assertEqual(fm.data["authorship"], "Codex")
        self.assertEqual(fm.conflicts, [])


class FirstDeclarationWinsIsUnchangedTests(unittest.TestCase):
    """Where block 1 DID declare the key, Task 1's behaviour must not move."""

    def test_a_redeclared_protected_key_still_keeps_the_first_value(self):
        fm = frontmatter.parse("---\nid: first\n---\n---\nid: second\n---\n\nbody\n")
        self.assertEqual(fm.data["id"], "first")

    def test_the_pinned_conflict_message_is_unchanged(self):
        fm = frontmatter.parse("---\nid: first\n---\n---\nid: second\n---\n\nbody\n")
        self.assertEqual(
            fm.conflicts,
            ["id: kept 'first' from block 1, rejected 'second' in block 2"],
        )

    def test_identical_redeclaration_is_still_silent(self):
        fm = frontmatter.parse("---\nid: same\ntitle: T\n---\n---\nid: same\n---\n\nbody\n")
        self.assertEqual(fm.data["id"], "same")
        self.assertEqual(fm.conflicts, [])


class DiagnosticsAreNotSerializedTests(unittest.TestCase):
    """§4 again: the new diagnostics must not reach extra either."""

    def test_establishment_diagnostics_never_land_in_extra(self):
        for key, (text, _value) in ESTABLISHED_LATE.items():
            with self.subTest(key=key):
                item, _suggested, fm = build_item(text)
                blob = repr(item.extra)
                for token in ("kept", "rejected", "conflict", "diagnostic"):
                    self.assertNotIn(token, blob.lower())


class TheAnchorIsTheFirstBlockDeclaringAProtectedKeyTests(unittest.TestCase):
    """The refined rule's distinctive behaviour.

    Under the amended wording the anchor is not always block 1. A provenance
    block declares no protected key, so a document may open with one and still
    establish its identity in block 2. What the rule forbids is a block AFTER
    the anchor reaching a protected key the anchor did not declare.
    """

    # block 1 provenance (no protected keys), block 2 identity => anchor is 2
    SEED_SHAPE = (
        "---\ncreated_via: textstrata-mcp\nauthorship: Codex\n---\n"
        "---\nid: note.anchored\ntitle: Anchored\ntype: note\ntags: [x]\n---\n"
    )

    def test_a_provenance_block_does_not_become_the_anchor(self):
        fm = frontmatter.parse(self.SEED_SHAPE + "\nbody\n")
        self.assertEqual(fm.data["id"], "note.anchored")
        self.assertEqual(fm.data["title"], "Anchored")
        self.assertEqual(fm.conflicts, [])

    def test_that_document_publishes(self):
        result = ingest_text(fresh_store(), self.SEED_SHAPE + "\nbody\n")
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(result.item.id, "note.anchored")

    def test_a_block_after_the_anchor_still_cannot_establish(self):
        text = self.SEED_SHAPE + "---\nrelated: [note.late]\n---\n\nbody\n"
        fm = frontmatter.parse(text)
        self.assertNotIn("related", fm.data)
        self.assertEqual(
            fm.conflicts,
            [establishment_message("related", 3, anchor_block=2)],
            "the diagnostic must name the establishing block and the anchor",
        )

    def test_a_block_after_the_anchor_may_still_add_unprotected_keys(self):
        text = self.SEED_SHAPE + "---\nsource_url: https://example.com\n---\n\nbody\n"
        fm = frontmatter.parse(text)
        self.assertEqual(fm.data["source_url"], "https://example.com")
        self.assertEqual(fm.conflicts, [])

    def test_the_anchor_may_be_the_only_block_declaring_anything_protected(self):
        result = ingest_text(fresh_store(), self.SEED_SHAPE + "---\ntags: [y]\n---\n\nbody\n")
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(result.item.tags[:2], ["x", "y"])


class AcceptGuardsTests(unittest.TestCase):
    """Legitimate shapes that must survive the new rule.

    These pass today. They are the reason the rule needs care: the parser exists
    precisely to support a provenance block stacked above a semantic block, which
    its own module docstring names as the motivating case.
    """

    def test_comment_bearing_front_matter_still_parses(self):
        fm = frontmatter.parse(
            "---\n# generated by a tool\nid: note.x\ntitle: T\ntype: note\ntags: [x]\n---\n\nbody\n"
        )
        self.assertEqual(fm.data["id"], "note.x")
        self.assertEqual(fm.warnings, [])
        self.assertEqual(fm.conflicts, [])

    def test_a_single_block_declaring_every_protected_key_is_fine(self):
        text = (
            "---\nid: note.all\ntitle: T\ntype: note\nhandling: human_plus_ai\n"
            "preservation: preserve_exact\ndependencies: [note.a]\nrelated: [note.b]\n"
            "tags: [x]\n---\n\nbody\n"
        )
        result = ingest_text(fresh_store(), text)
        self.assertTrue(result.published, result.validation.errors)

    def test_the_seed_corpus_still_ingests(self):
        """The motivating case, pinned as accepted.

        The seed's first block is provenance only, so it declares no protected
        key; its second block establishes id, title and type and is therefore
        the anchor. This is the shape the earlier "block 1 only" wording
        rejected, which is why it was amended.
        """
        seed = Path(__file__).resolve().parents[1] / "seed" / "textstrata-architecture.md"
        result = ingest_text(fresh_store(), seed.read_text(encoding="utf-8"))
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(result.item.id, "textstrata.architecture")
        self.assertTrue(result.had_stacked_frontmatter)

    def test_a_provenance_block_above_a_semantic_block_still_works(self):
        text = (
            "---\ncreated_via: textstrata-mcp\nauthorship: Codex\n---\n"
            "---\nid: note.stacked\ntitle: Stacked\ntype: note\ntags: [x]\n---\n\nbody\n"
        )
        result = ingest_text(fresh_store(), text)
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(result.item.id, "note.stacked")
        self.assertEqual(result.item.provenance.created_via, "textstrata-mcp")


if __name__ == "__main__":
    unittest.main()
