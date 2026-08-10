import unittest

from textstrata import classify
from textstrata.models import (
    ContentType,
    TextStrataItem,
    HandlingMode,
    PreservationMode,
    is_valid_id,
)
from textstrata.validate import validate


class CoercionTests(unittest.TestCase):
    def test_content_type_coercion(self):
        self.assertIs(ContentType.coerce("architecture-note"), ContentType.ARCHITECTURE_NOTE)
        self.assertIs(ContentType.coerce("Anti Pattern"), ContentType.ANTI_PATTERN)
        self.assertIs(ContentType.coerce("nonsense"), ContentType.NOTE)
        self.assertIs(ContentType.coerce(None), ContentType.NOTE)

    def test_mode_coercion_defaults(self):
        self.assertIs(HandlingMode.coerce("human-only"), HandlingMode.HUMAN_ONLY)
        self.assertIs(HandlingMode.coerce(""), HandlingMode.UNSET)
        self.assertIs(PreservationMode.coerce("rewrite allowed"), PreservationMode.REWRITE_ALLOWED)
        self.assertIs(PreservationMode.coerce(None), PreservationMode.PRESERVE_EXACT)

    def test_id_validation(self):
        self.assertTrue(is_valid_id("promptguru.textstrata-architecture"))
        self.assertTrue(is_valid_id("a_b.c-d"))
        self.assertFalse(is_valid_id("Has Spaces"))
        self.assertFalse(is_valid_id("UPPER"))
        self.assertFalse(is_valid_id(""))


class ValidationTests(unittest.TestCase):
    def _item(self, **kw):
        base = dict(id="a.b", type=ContentType.NOTE, title="T")
        base.update(kw)
        return TextStrataItem(**base)

    def test_valid_item_passes(self):
        r = validate(self._item(tags=["x"]))
        self.assertTrue(r.ok)
        self.assertEqual(r.errors, [])

    def test_bad_id_fails(self):
        r = validate(self._item(id="Bad Id"))
        self.assertFalse(r.ok)

    def test_missing_title_fails(self):
        r = validate(self._item(title="  "))
        self.assertFalse(r.ok)

    def test_contradictory_policy_fails(self):
        r = validate(self._item(
            handling=HandlingMode.HUMAN_ONLY,
            preservation=PreservationMode.REWRITE_ALLOWED,
        ))
        self.assertFalse(r.ok)
        self.assertTrue(any("policy conflict" in e for e in r.errors))

    def test_no_tags_warns_but_passes(self):
        r = validate(self._item(tags=[]))
        self.assertTrue(r.ok)
        self.assertTrue(r.warnings)

    def test_duplicate_collections_are_rejected(self):
        r = validate(self._item(tags=["Security", "security"], aliases=["Same", "same"]))
        self.assertFalse(r.ok)
        self.assertTrue(any("duplicate tags" in error for error in r.errors))
        self.assertTrue(any("duplicate aliases" in error for error in r.errors))


class ClassifyTests(unittest.TestCase):
    def test_explicit_type_wins(self):
        self.assertIs(
            classify.detect_type("decision-record", "t", "some body"),
            ContentType.DECISION_RECORD,
        )

    def test_structural_fallback(self):
        self.assertIs(
            classify.detect_type(None, "Our Architecture", "architectural overview"),
            ContentType.ARCHITECTURE_NOTE,
        )
        self.assertIs(
            classify.detect_type(None, "snippet", "```python\nx=1\n```"),
            ContentType.CODE_SAMPLE,
        )

    def test_tag_suggestion_is_additive_and_deduped(self):
        suggested = classify.suggest_tags(
            "Security", "we set a CSP and block SSRF", declared=["security"]
        )
        self.assertNotIn("security", suggested)  # already declared
        suggested2 = classify.suggest_tags(
            "MCP note", "connect via MCP for RAG", declared=[]
        )
        self.assertIn("mcp", suggested2)
        self.assertIn("rag", suggested2)


if __name__ == "__main__":
    unittest.main()
