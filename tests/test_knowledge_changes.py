"""Article proposals are inert until review and reject stale updates."""

import tempfile
import unittest

from textstrata.ingest import _update_text
from textstrata.knowledge_changes import finish_article_change, propose_article_change
from textstrata.store import TextStrataStore


def article(body="Original body", *, model="synthetic-model"):
    return (
        "---\nid: example-change\ntitle: Example Change\ntype: reference\ntags: [example]\n"
        "provenance:\n  origin: ai\n  contributor_chain: via_ai\n"
        f"  ai_vendor: Test Vendor\n  ai_model: {model}\n---\n\n# Example Change\n\n{body}\n"
    )


class KnowledgeChangeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TextStrataStore(temp.name)

    def test_create_is_inert_until_explicit_review(self):
        proposed = propose_article_change(self.store, "create", article(), "new durable fact")
        same = propose_article_change(self.store, "create", article(), "new durable fact")
        self.assertEqual(proposed["proposal_id"], same["proposal_id"])
        self.assertIsNone(self.store.normalized_path_for_id("example-change"))
        finished = finish_article_change(self.store, proposed["proposal_id"], apply=True, reviewer="test-reviewer")
        self.assertEqual(finished["status"], "applied")
        self.assertIn("Original body", self.store.normalized_path_for_id("example-change").read_text())

    def test_stale_update_cannot_overwrite_newer_local_content(self):
        _update_text(self.store, article())
        proposed = propose_article_change(self.store, "update", article("Proposed body"), "update fact")
        _update_text(self.store, article("Newer local body"))
        with self.assertRaisesRegex(ValueError, "article changed since proposal"):
            finish_article_change(self.store, proposed["proposal_id"], apply=True, reviewer="test-reviewer")
        self.assertIn("Newer local body", self.store.normalized_path_for_id("example-change").read_text())

    def test_reject_does_not_publish(self):
        proposed = propose_article_change(self.store, "create", article(), "not accepted")
        finished = finish_article_change(self.store, proposed["proposal_id"], apply=False, reviewer="test-reviewer")
        self.assertEqual(finished["status"], "rejected")
        self.assertIsNone(self.store.normalized_path_for_id("example-change"))

    def test_ai_model_is_required_for_ai_authored_proposal(self):
        with self.assertRaisesRegex(ValueError, "ai_vendor and exact ai_model"):
            propose_article_change(self.store, "create", article(model="").replace("  ai_model: \n", ""), "reason")


if __name__ == "__main__":
    unittest.main()
