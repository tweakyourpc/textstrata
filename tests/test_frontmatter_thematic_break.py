"""Task 4 — a thematic break must not be mistaken for front matter.

``_LEADING_BLOCK_RE`` matches any ``---`` ... ``---`` pair at the top of a
document. A Markdown document that opens with a thematic break, or whose body
opens with one directly under real front matter, therefore has the text between
the two rules swallowed as a "block". It parses to a non-mapping, gets stashed
under the ``_nonmapping`` key, and the prose is gone from the body.

Scope correction C2: rejection stops at the offending candidate. Blocks already
accepted before it are kept; the candidate and everything after it become body.
A warning is emitted so the caller can see the parse decision.
"""

import unittest
from pathlib import Path

from textstrata import frontmatter


OPENS_WITH_BREAK = "---\n\n# Heading\n\nFirst para.\n\n---\n\nSecond para.\n"

FRONTMATTER_THEN_BREAK = (
    "---\n"
    "id: note.x\n"
    "title: T\n"
    "type: note\n"
    "---\n"
    "---\n"
    "\n"
    "Body one.\n"
    "\n"
    "---\n"
    "\n"
    "Body two.\n"
)


class DocumentOpeningWithAThematicBreakTests(unittest.TestCase):
    def test_no_frontmatter_is_consumed(self):
        fm = frontmatter.parse(OPENS_WITH_BREAK)
        self.assertEqual(fm.data, {})
        self.assertEqual(fm.block_count, 0)

    def test_the_whole_document_stays_in_the_body(self):
        fm = frontmatter.parse(OPENS_WITH_BREAK)
        self.assertIn("# Heading", fm.body)
        self.assertIn("First para.", fm.body)
        self.assertIn("Second para.", fm.body)


class FrontmatterFollowedByAThematicBreakTests(unittest.TestCase):
    """C2: the accepted block survives; rejection stops at the candidate."""

    def test_the_accepted_front_matter_block_is_kept(self):
        fm = frontmatter.parse(FRONTMATTER_THEN_BREAK)
        self.assertEqual(fm.data["id"], "note.x")
        self.assertEqual(fm.data["title"], "T")
        self.assertEqual(fm.data["type"], "note")

    def test_only_the_accepted_block_is_counted(self):
        fm = frontmatter.parse(FRONTMATTER_THEN_BREAK)
        self.assertEqual(fm.block_count, 1)

    def test_the_candidate_and_everything_after_it_become_body(self):
        fm = frontmatter.parse(FRONTMATTER_THEN_BREAK)
        self.assertTrue(
            fm.body.startswith("---"),
            f"the thematic break was consumed instead of kept: {fm.body[:40]!r}",
        )
        self.assertIn("Body one.", fm.body)
        self.assertIn("Body two.", fm.body)


class NonMappingIsNeverSurfacedAsDataTests(unittest.TestCase):
    def test_prose_never_lands_under_a_nonmapping_key(self):
        for label, text in (
            ("opens with break", OPENS_WITH_BREAK),
            ("front matter then break", FRONTMATTER_THEN_BREAK),
        ):
            with self.subTest(case=label):
                fm = frontmatter.parse(text)
                self.assertNotIn("_nonmapping", fm.data)


class RejectionIsReportedTests(unittest.TestCase):
    """§4 adds ``MergedFrontmatter.warnings`` for parser diagnostics."""

    def test_a_rejected_candidate_emits_one_warning(self):
        fm = frontmatter.parse(FRONTMATTER_THEN_BREAK)
        self.assertEqual(len(fm.warnings), 1)
        self.assertTrue(fm.warnings[0].strip())

    def test_a_clean_document_emits_no_warnings(self):
        fm = frontmatter.parse("---\nid: a\ntitle: A\n---\n\nbody\n")
        self.assertEqual(fm.warnings, [])

    def test_warnings_are_not_conflicts(self):
        fm = frontmatter.parse(FRONTMATTER_THEN_BREAK)
        self.assertEqual(fm.conflicts, [])


class GenuineStackedBlocksStillParseTests(unittest.TestCase):
    """The rejection rule must not cost us real stacked front matter."""

    def test_two_real_mapping_blocks_still_merge(self):
        text = (
            "---\ncreated_via: textstrata-mcp\n---\n"
            "---\nid: x.y\ntitle: Stacked\n---\n\n# Stacked\n\nbody\n"
        )
        fm = frontmatter.parse(text)
        self.assertEqual(fm.block_count, 2)
        self.assertEqual(fm.data["created_via"], "textstrata-mcp")
        self.assertEqual(fm.data["id"], "x.y")
        self.assertEqual(fm.body.strip().splitlines()[0], "# Stacked")



FRONT_MATTER = "---\nid: note.x\ntitle: T\ntype: note\ntags: [x]\n---\n"


def with_second_block(candidate: str) -> str:
    return FRONT_MATTER + f"---\n{candidate}---\n\nAfter the break.\n"


class ProseThatFailsYamlIsStillProseTests(unittest.TestCase):
    """Verification gap found while checking Batch B end to end (§6).

    ``_looks_like_prose`` gives up in two places before it ever reaches the
    Markdown-heading test.

    It returns ``False`` on ``yaml.YAMLError``, treating "this is not valid
    YAML" as evidence that a block *is* front matter. It is the opposite:
    salvage exists for the rare invalid-YAML front matter, but the overwhelming
    majority of unparseable candidates are ordinary prose with a colon in it.

    And ``#`` starts a comment in YAML, so a Markdown heading is invisible to
    the parser. A prose section of ``# Heading`` plus one ``Note: ...`` line
    parses cleanly as the mapping ``{'Note': '...'}`` and is accepted as a
    mapping block before the heading check can object.

    The result is that a Markdown section between two thematic breaks is
    swallowed exactly as it was before Task 4, provided it contains a colon --
    which ordinary technical prose usually does. Worse, salvage then turns its
    lines into keys, so body text can set ``id``, ``title`` or ``type`` on an
    item that never declared them. There is no conflict, because block 1 said
    nothing, and no warning.

    Two-line candidates such as ``# Notes`` plus a single ``See: ...`` line are
    genuinely ambiguous and are deliberately not asserted here. The shapes below
    are not: they carry a heading, or prose lines outnumbering key lines, or
    both. The accept-guards matter as much as the reject cases -- the seed
    corpus is invalid YAML on purpose, and front matter may carry comments.
    """

    PROSE = {
        "heading then a paragraph then a colon line":
            "\n# Heading\n\nFirst paragraph.\n\nNote: important.\n",
        "heading then a title-shaped line then narrative":
            "\n## Section\n\ntitle: The Great Escape\n\nnarrative follows.\n",
        "prose lines outnumbering key lines":
            "\nNote: important.\n\nFirst para.\n\nSecond para.\n\nThird para.\n",
    }

    MUST_STAY_FRONT_MATTER = {
        "seed-shaped invalid YAML":
            "\nid: seed.arch\ntitle: TextStrata: A Machine-First Substrate\n"
            "type: architecture-note\nversion: 1.0.0\ntags: [a, b]\n",
        "front matter carrying a YAML comment":
            "\n# generated by a tool\ncreated_via: textstrata-mcp\nauthorship: Codex\n",
        "clean front matter":
            "\ncreated_via: textstrata-mcp\nauthorship: Codex\n",
    }

    def test_prose_candidates_are_rejected(self):
        for label, candidate in self.PROSE.items():
            with self.subTest(shape=label):
                fm = frontmatter.parse(with_second_block(candidate))
                self.assertEqual(fm.block_count, 1, f"{label}: accepted as front matter")
                self.assertEqual(len(fm.warnings), 1)

    def test_rejected_prose_stays_in_the_body(self):
        for label, candidate in self.PROSE.items():
            with self.subTest(shape=label):
                fm = frontmatter.parse(with_second_block(candidate))
                for line in (l.strip() for l in candidate.splitlines() if l.strip()):
                    self.assertIn(line, fm.body, f"{label}: {line!r} was swallowed")
                self.assertIn("After the break.", fm.body)

    def test_prose_never_becomes_front_matter_keys(self):
        for label, candidate in self.PROSE.items():
            with self.subTest(shape=label):
                fm = frontmatter.parse(with_second_block(candidate))
                self.assertEqual(
                    set(fm.data), {"id", "title", "type", "tags"},
                    f"{label}: prose lines leaked in as keys",
                )
                # A prose line whose key block 1 already declared adds no new
                # key -- it surfaces as a conflict instead. Prose that was never
                # parsed as front matter cannot conflict with anything.
                self.assertEqual(
                    fm.conflicts, [], f"{label}: prose was merged and conflicted"
                )

    def test_prose_cannot_supply_an_identity_the_document_never_declared(self):
        # Block 1 declares no title, so nothing conflicts and nothing warns:
        # the narrative simply becomes the item's title.
        text = (
            "---\nid: note.x\ntype: note\ntags: [x]\n---\n"
            "---\n\n## Section\n\ntitle: The Great Escape\n\nnarrative follows.\n"
            "---\n\nAfter the break.\n"
        )
        fm = frontmatter.parse(text)
        self.assertNotIn("title", fm.data, "body prose set the item's title")

    def test_real_front_matter_is_still_accepted(self):
        for label, candidate in self.MUST_STAY_FRONT_MATTER.items():
            with self.subTest(shape=label):
                fm = frontmatter.parse(with_second_block(candidate))
                self.assertEqual(fm.block_count, 2, f"{label}: real front matter was rejected as prose")
                self.assertEqual(fm.warnings, [])

    def test_the_seed_corpus_still_parses_as_front_matter(self):
        seed = Path(__file__).resolve().parents[1] / "seed" / "textstrata-architecture.md"
        fm = frontmatter.parse(seed.read_text(encoding="utf-8"))
        self.assertEqual(fm.block_count, 2)
        self.assertEqual(fm.data["id"], "textstrata.architecture")
        self.assertEqual(fm.warnings, [])


if __name__ == "__main__":
    unittest.main()
