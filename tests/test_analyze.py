import unittest
from datetime import datetime, timezone

from textstrata.analyze import analyze, _days_since
from textstrata.models import ContentType, TextStrataItem, HandlingMode, PreservationMode, Provenance


def _item(item_id: str, **kw) -> TextStrataItem:
    base = dict(
        id=item_id,
        type=ContentType.NOTE,
        title="T",
        tags=[],
        related=[],
        dependencies=[],
        handling=HandlingMode.HUMAN_PLUS_AI,
        preservation=PreservationMode.PRESERVE_EXACT,
        retrieval_priority=0,
        provenance=Provenance(),
        body="body",
        extra={},
    )
    base.update(kw)
    return TextStrataItem(**base)


class DaysSinceTests(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_days_since(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_days_since(""))

    def test_recent_date_returns_small_float(self):
        now = datetime.now(timezone.utc)
        days = _days_since(now.isoformat())
        self.assertIsNotNone(days)
        self.assertLess(days, 1)

    def test_old_date_returns_large_float(self):
        days = _days_since("2020-01-01T00:00:00Z")
        self.assertIsNotNone(days)
        self.assertGreater(days, 180)

    def test_bad_date_returns_none(self):
        self.assertIsNone(_days_since("not-a-date"))


class AnalyzeTests(unittest.TestCase):
    def test_empty_items_returns_empty_report(self):
        report = analyze([])
        self.assertEqual(report["total_items"], 0)

    def test_counts_by_type(self):
        items = [
            _item("a", type=ContentType.POLICY),
            _item("b", type=ContentType.POLICY),
            _item("c", type=ContentType.ARCHITECTURE_NOTE),
        ]
        report = analyze(items)
        self.assertEqual(report["total_items"], 3)
        self.assertEqual(report["by_type"]["policy"], 2)
        self.assertEqual(report["by_type"]["architecture_note"], 1)

    def test_no_tags_reported(self):
        items = [_item("a", tags=[]), _item("b", tags=["x"])]
        report = analyze(items)
        self.assertIn("a", report["no_tags"])
        self.assertNotIn("b", report["no_tags"])

    def test_incident_without_resolution_reported(self):
        items = [_item("incident-a", type=ContentType.INCIDENT, extra={})]
        report = analyze(items)
        self.assertIn("incident-a", report["no_resolution"])

    def test_incident_with_resolution_omitted(self):
        items = [_item("incident-b", type=ContentType.INCIDENT, extra={"resolution": "fixed it"})]
        report = analyze(items)
        self.assertNotIn("incident-b", report["no_resolution"])

    def test_known_error_without_resolution_reported(self):
        items = [_item("ke-a", type=ContentType.KNOWN_ERROR, extra={})]
        report = analyze(items)
        self.assertIn("ke-a", report["no_resolution"])

    def test_low_priority_incidents_reported(self):
        items = [_item("low-incident", type=ContentType.INCIDENT, retrieval_priority=0, extra={})]
        report = analyze(items)
        self.assertIn("low-incident", report["low_priority_incidents"])

    def test_stale_item_detected(self):
        items = [_item("stale-item", extra={"last_edited_at": "2020-06-15T00:00:00Z"})]
        report = analyze(items)
        self.assertEqual(len(report["stale_items"]), 1)
        self.assertEqual(report["stale_items"][0]["id"], "stale-item")

    def test_fresh_item_not_stale(self):
        now = datetime.now(timezone.utc).isoformat()
        items = [_item("fresh-item", extra={"last_edited_at": now})]
        report = analyze(items)
        self.assertEqual(len(report["stale_items"]), 0)

    def test_orphaned_items(self):
        items = [
            _item("a", related=["b"]),
            _item("b"),
            _item("c"),
        ]
        report = analyze(items)
        self.assertIn("a", report["orphaned_items"])
        self.assertIn("c", report["orphaned_items"])
        self.assertNotIn("b", report["orphaned_items"])

    def test_top_tags(self):
        items = [
            _item("a", tags=["security", "python"]),
            _item("b", tags=["security", "rag"]),
        ]
        report = analyze(items)
        self.assertEqual(report["top_tags"]["security"], 2)
        self.assertEqual(report["top_tags"]["python"], 1)

    def test_missing_fields_for_incident(self):
        items = [_item("incident-c", type=ContentType.INCIDENT, extra={"symptom": "crash"})]
        report = analyze(items)
        self.assertIn("incident-c", report["missing_fields"])
        missing = report["missing_fields"]["incident-c"]
        self.assertIn("resolution", missing)
        self.assertIn("environment", missing)

    def test_summary_counts(self):
        items = [
            _item("a", type=ContentType.INCIDENT, tags=[], extra={}),
            _item("b", type=ContentType.KNOWN_ERROR, tags=[], extra={}),
            _item("c", type=ContentType.NOTE, tags=["x"]),
        ]
        report = analyze(items)
        s = report["summary"]
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["incident_types"], 2)
        self.assertEqual(s["unresolved"], 2)
        self.assertEqual(s["untagged"], 2)

    def test_single_item_not_orphaned(self):
        items = [_item("solo")]
        report = analyze(items)
        self.assertEqual(report["orphaned_items"], [])


if __name__ == "__main__":
    unittest.main()
