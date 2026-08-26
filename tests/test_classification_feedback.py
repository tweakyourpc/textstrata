"""Classification suggestions are recorded at ingest and reported when overridden.

EditRecord gained ``suggested_policy`` and ``suggested_tags`` so what the rules
proposed can be compared against what was actually approved, and
``show-misclassifications`` surfaces the disagreements.

The load-bearing test here is determinism. ``canonical_frontmatter`` serializes
``edited_by`` into ``extra``, so anything time-varying on that record leaks straight
into the published Markdown and identical input stops normalizing to identical bytes.
That is the invariant ``test_determinism`` exists to protect, and recording a
classification on every publish walks directly into it, so the ingest record carries no
timestamp and this file pins that.
"""

import tempfile
import time
import unittest
from pathlib import Path

from textstrata import ingest
from textstrata.classify import PolicySuggestion
from textstrata.commands.misclassifications import format_report, scan
from textstrata.models import EditRecord, HandlingMode, Origin, PreservationMode
from textstrata.store import TextStrataStore

POLICY_DECLARING_NOTHING = """---
id: policy.silent
type: policy
title: Silent Policy
---

Normative rules live here.
"""

POLICY_OVERRIDING = """---
id: policy.override
type: policy
title: Overriding Policy
handling: human_plus_ai
preservation: summarize_allowed
---

Normative rules live here too.
"""

CODE_SAMPLE = """---
id: code.sample
type: code_sample
title: Example Code
---

    def hello():
        print("world")
"""


class StoreTestCase(unittest.TestCase):
    def fresh_store(self) -> TextStrataStore:
        """A store whose directory lives exactly as long as the test."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return TextStrataStore(Path(tmp.name))


class EditRecordSuggestionTests(unittest.TestCase):
    def test_a_suggested_policy_and_tags_survive_a_round_trip(self):
        record = EditRecord(
            origin=Origin.VIA_SCRIPT,
            suggested_policy=PolicySuggestion(
                handling=HandlingMode.HUMAN_PLUS_AI,
                preservation=PreservationMode.SUMMARIZE_ALLOWED,
                rationale="reference material",
            ),
            suggested_tags=["code", "python"],
            description="ingest classification",
        )
        data = record.to_dict()
        self.assertIn("suggested_policy", data)
        self.assertIn("suggested_tags", data)

        restored = EditRecord.from_dict(data)
        self.assertIsNotNone(restored.suggested_policy)
        self.assertEqual(restored.suggested_tags, ["code", "python"])
        self.assertEqual(restored.suggested_policy.handling, HandlingMode.HUMAN_PLUS_AI)
        self.assertEqual(
            restored.suggested_policy.preservation, PreservationMode.SUMMARIZE_ALLOWED
        )
        self.assertEqual(restored.suggested_policy.rationale, "reference material")
        self.assertEqual(restored.origin, Origin.VIA_SCRIPT)
        self.assertEqual(restored.description, "ingest classification")

    def test_a_record_without_suggestions_serializes_exactly_as_before(self):
        """Already-published items must not change shape because the fields exist."""
        data = EditRecord(origin=Origin.HUMAN, timestamp="fixed").to_dict()
        self.assertEqual(set(data), {"origin", "timestamp", "drift", "description"})

    def test_an_unrecognized_stored_policy_degrades_instead_of_raising(self):
        restored = EditRecord.from_dict(
            {"origin": "human", "suggested_policy": {"handling": "nonsense"}}
        )
        self.assertIsNotNone(restored.suggested_policy)
        self.assertEqual(restored.suggested_policy.handling, HandlingMode.UNSET)

    def test_a_scalar_suggested_tag_is_read_as_a_one_item_list(self):
        """A bare scalar must not be iterated into characters."""
        self.assertEqual(EditRecord.from_dict({"suggested_tags": "solo"}).suggested_tags, ["solo"])

    def test_a_missing_suggested_policy_stays_none(self):
        self.assertIsNone(EditRecord.from_dict({"origin": "human"}).suggested_policy)


class IngestRecordsSuggestionsTests(StoreTestCase):
    def test_ingest_attaches_the_suggestion_to_the_edit_history(self):
        result = ingest.ingest_text(self.fresh_store(), POLICY_DECLARING_NOTHING)
        self.assertTrue(result.published, result.validation.errors)
        self.assertTrue(result.item.edited_by)

        record = result.item.edited_by[-1]
        self.assertIsNotNone(record.suggested_policy)
        self.assertIsNotNone(record.suggested_tags)
        self.assertEqual(record.origin, Origin.VIA_SCRIPT)

    def test_a_code_sample_is_suggested_exact_and_human_only(self):
        result = ingest.ingest_text(self.fresh_store(), CODE_SAMPLE)
        self.assertTrue(result.published, result.validation.errors)

        suggestion = result.item.edited_by[-1].suggested_policy
        self.assertEqual(suggestion.preservation, PreservationMode.PRESERVE_EXACT)
        self.assertEqual(suggestion.handling, HandlingMode.HUMAN_ONLY)

    def test_the_suggestion_reaches_the_published_frontmatter(self):
        result = ingest.ingest_text(self.fresh_store(), POLICY_DECLARING_NOTHING)
        published = result.normalized_path.read_text(encoding="utf-8")
        self.assertIn("edited_by:", published)
        self.assertIn("suggested_policy:", published)

    def test_the_ingest_record_carries_no_timestamp(self):
        """A generated timestamp here would leak into every published file."""
        result = ingest.ingest_text(self.fresh_store(), POLICY_DECLARING_NOTHING)
        self.assertEqual(result.item.edited_by[-1].timestamp, "")

    def test_identical_input_still_normalizes_to_identical_bytes(self):
        first = ingest.ingest_text(self.fresh_store(), POLICY_DECLARING_NOTHING)
        time.sleep(0.01)
        second = ingest.ingest_text(self.fresh_store(), POLICY_DECLARING_NOTHING)
        self.assertEqual(
            first.normalized_path.read_bytes(),
            second.normalized_path.read_bytes(),
            "recording a classification made normalized output time-dependent",
        )

    def test_republishing_does_not_accumulate_classification_records(self):
        store = self.fresh_store()
        for _ in range(3):
            result = ingest.ingest_text(store, POLICY_DECLARING_NOTHING)
        self.assertEqual(len(result.item.edited_by), 1)

    def test_an_unrelated_edit_record_survives_the_update_path(self):
        """The update path re-ingests the published file, so its records must carry over.

        Re-ingesting the pristine source instead would have nothing to preserve: an item
        is built from the text handed to ingest, and that text carries no edit history.
        """
        store = self.fresh_store()
        ingest.ingest_text(store, POLICY_DECLARING_NOTHING)
        path = store.normalized_path_for_id("policy.silent")
        edited = path.read_text(encoding="utf-8").replace(
            "  edited_by:",
            "  edited_by:\n  - origin: human\n    timestamp: fixed\n    drift: 0.0\n"
            "    description: hand edit",
            1,
        )
        self.assertIn("hand edit", edited, "test fixture failed to inject the record")

        result = ingest._update_text(store, edited, fallback_id="policy.silent")
        descriptions = [r.description for r in result.item.edited_by]
        self.assertIn("hand edit", descriptions)
        self.assertEqual(
            descriptions.count("automatic classification at ingest"),
            1,
            "the classification record was duplicated rather than replaced",
        )


class MisclassificationReportTests(StoreTestCase):
    def test_a_declared_override_is_reported(self):
        store = self.fresh_store()
        ingest.ingest_text(store, POLICY_OVERRIDING)

        outcome = scan(store.root)
        self.assertEqual(len(outcome.mismatches), 1)
        entry = outcome.mismatches[0]
        self.assertEqual(entry["item_id"], "policy.override")
        self.assertTrue(entry["policy_overridden"])
        self.assertEqual(entry["suggested_handling"], "human_only")
        self.assertEqual(entry["actual_handling"], "human_plus_ai")
        self.assertIn("policy.override", format_report(outcome.mismatches, outcome))

    def test_a_suggestion_nobody_declared_against_is_counted_not_reported(self):
        """An unadopted suggestion is not a disagreement."""
        store = self.fresh_store()
        ingest.ingest_text(store, POLICY_DECLARING_NOTHING)

        outcome = scan(store.root)
        self.assertEqual(outcome.mismatches, [])
        self.assertEqual(outcome.unapplied, 1)

    def test_a_store_of_items_without_records_reports_nothing(self):
        """Items published before the fields existed must not appear."""
        store = self.fresh_store()
        result = ingest.ingest_text(store, POLICY_OVERRIDING)
        path = result.normalized_path
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
            if "edited_by" not in line
            and "suggested_" not in line
            and "automatic classification" not in line
        ]
        path.write_text("".join(lines), encoding="utf-8")

        outcome = scan(store.root)
        self.assertEqual(outcome.mismatches, [])
        self.assertEqual(outcome.scanned, 1)

    def test_an_unreadable_file_is_counted_rather_than_raised(self):
        store = self.fresh_store()
        ingest.ingest_text(store, POLICY_OVERRIDING)
        (store.normalized_dir / "broken.md").write_bytes(b"\xff\xfe not utf-8 at all")

        outcome = scan(store.root)
        self.assertEqual(len(outcome.skipped), 1)
        self.assertIn("broken.md", outcome.skipped[0])
        self.assertEqual(len(outcome.mismatches), 1, "a bad file must not hide good findings")

    def test_an_empty_store_reports_no_misclassifications(self):
        self.assertEqual(scan(Path(tempfile.mkdtemp())).mismatches, [])
        self.assertIn("No misclassifications found", format_report([]))


if __name__ == "__main__":
    unittest.main()
