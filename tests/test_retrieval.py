import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from textstrata.catalog import Catalog
from textstrata.__main__ import cmd_retrieval_inspect
from textstrata.ingest import ingest_text
from textstrata.research import research
from textstrata.retrieval import extract_keywords, retrieve
from textstrata.store import TextStrataStore


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "retrieval"


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TextStrataStore(self.tmp.name)
        for path in sorted(FIXTURE_ROOT.glob("*.md")):
            result = ingest_text(self.store, path.read_text(encoding="utf-8"))
            self.assertTrue(result.published, result.validation.errors)
        self.catalog = Catalog(self.tmp.name)
        self.catalog.rescan(self.store)

    def tearDown(self):
        self.catalog.close()
        self.tmp.cleanup()

    def test_fixture_queries_retrieve_expected_items(self):
        fixture = json.loads((FIXTURE_ROOT / "relevance.json").read_text(encoding="utf-8"))
        for case in fixture["queries"]:
            with self.subTest(query=case["query"]):
                result = retrieve(case["query"], self.catalog, self.store)
                ids = [candidate.item_id for candidate in result.candidates]
                self.assertTrue(result.sufficient_evidence)
                self.assertTrue(set(case["relevant"]).issubset(ids))

    def test_query_normalization_is_deterministic(self):
        self.assertEqual(
            extract_keywords("How does the local model inference work?"),
            ("local", "model", "inference"),
        )

    def test_trace_contains_selected_chunk_and_score_breakdown(self):
        result = retrieve("browser keyboard navigation", self.catalog, self.store, limit=1)
        payload = result.to_dict()
        self.assertEqual(payload["strategy"], "keyword")
        self.assertEqual(payload["candidates"][0]["item_id"], "ui.navigation")
        self.assertEqual(payload["candidates"][0]["matched_terms"], ["browser", "keyboard", "navigation"])
        self.assertIn("chunk", payload["candidates"][0])
        self.assertGreater(payload["evidence_score"], 0.0)

    def test_no_usable_terms_is_an_evidence_gap(self):
        result = retrieve("the and of", self.catalog, self.store)
        self.assertFalse(result.sufficient_evidence)
        self.assertEqual(result.reason, "no usable query terms")
        self.assertEqual(result.candidates, ())

    def test_missing_match_is_an_evidence_gap(self):
        result = retrieve("quantum gravity", self.catalog, self.store)
        self.assertFalse(result.sufficient_evidence)
        self.assertEqual(result.reason, "no body-bearing candidates")

    def test_research_uses_shared_candidates_and_calls_model_only_with_evidence(self):
        with patch("textstrata.research._call_ollama", return_value="grounded answer [1]") as call:
            result = research("backup manifest", self.store, self.catalog)
        self.assertEqual(result.sources[0].item_id, "ops.backup")
        self.assertEqual(result.sources[0].chunk, retrieve("backup manifest", self.catalog, self.store).candidates[0].chunk)
        call.assert_called_once()

    def test_research_returns_gap_without_calling_model(self):
        with patch("textstrata.research._call_ollama") as call:
            result = research("quantum gravity", self.store, self.catalog)
        self.assertIn("No relevant content", result.answer)
        call.assert_not_called()

    def test_cli_retrieval_inspect_emits_json_trace(self):
        output = StringIO()
        with patch("textstrata.__main__._root", return_value=Path(self.tmp.name)):
            with redirect_stdout(output):
                status = cmd_retrieval_inspect("local model inference", json_output=True)
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(payload["sufficient_evidence"])
        self.assertEqual(payload["candidates"][0]["item_id"], "ai.local-models")


if __name__ == "__main__":
    unittest.main()
