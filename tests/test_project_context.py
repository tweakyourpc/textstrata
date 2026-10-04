"""Project bootstrap selects only declared current knowledge."""

import tempfile
import unittest

from textstrata.context import get_project_context, render_project_context
from textstrata.ingest import ingest_text
from textstrata.store import TextStrataStore


def article(item_id, tier, status="current", related="[]", summary="Current fact"):
    return (
        f"---\nid: {item_id}\ntitle: {item_id}\ntype: reference\ntags: [project]\n"
        f"related: {related}\ncontext:\n  projects: [neoforge]\n  tier: {tier}\n"
        f"  status: {status}\n  summary: {summary}\n---\n\n# {item_id}\n\nConcrete body.\n"
    )


class ProjectContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TextStrataStore(self.tmp.name)

    def publish(self, text):
        result = ingest_text(self.store, text)
        self.assertTrue(result.published, result.validation.errors)

    def test_tiers_are_stable_and_exclude_superseded_context(self):
        self.publish(article("current-architecture", 1, related="[current-procedure, historical-note]"))
        self.publish(article("current-procedure", 2, summary="What to do next"))
        self.publish(article("historical-note", 3, status="superseded"))
        first = get_project_context(self.store, "neoforge")
        second = get_project_context(self.store, "neoforge")
        self.assertEqual(first, second)
        self.assertEqual([r["id"] for r in first["tier1"]], ["current-architecture"])
        self.assertEqual([r["id"] for r in first["tier2"]], ["current-procedure"])
        self.assertEqual([r["id"] for r in first["tier3"]], ["historical-note"])
        self.assertIn("Concrete body", render_project_context(first))

    def test_invalid_context_is_rejected_before_publication(self):
        result = ingest_text(self.store, article("bad-status", 1, status="guess"))
        self.assertFalse(result.published)
        self.assertIn("context.status", " ".join(result.validation.errors))
        self.assertIsNone(self.store.normalized_path_for_id("bad-status"))

    def test_no_current_tier_one_fails_closed(self):
        self.publish(article("old-architecture", 1, status="historical"))
        with self.assertRaisesRegex(ValueError, "no current tier-1"):
            get_project_context(self.store, "neoforge")

    def test_missing_superseded_article_fails_closed(self):
        self.publish(article("current-architecture", 1).replace("---\n\n#", "  supersedes: [missing-article]\n---\n\n#", 1))
        with self.assertRaisesRegex(ValueError, "supersedes missing article"):
            get_project_context(self.store, "neoforge")


if __name__ == "__main__":
    unittest.main()
