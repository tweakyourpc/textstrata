from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from textstrata.google_drive import GoogleDriveAdapter, GoogleDriveConfig, _acknowledge, _redact_error, _validate_oauth_scopes, extract_google_doc, ingest_google, parse_manifest
from textstrata.store import TextStrataStore


class FakeRequest:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeValues:
    def __init__(self, values, *, fail_update=False):
        self.values = values
        self.updates = []
        self.fail_update = fail_update

    def get(self, **kwargs):
        return FakeRequest({"values": self.values})

    def batchUpdate(self, **kwargs):
        if self.fail_update:
            raise RuntimeError("503 temporary sheet failure")
        self.updates.append(kwargs)
        return FakeRequest({})


class FakeSheets:
    def __init__(self, values, *, fail_update=False):
        self._values = FakeValues(values, fail_update=fail_update)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


class FakeDocs:
    def __init__(self, documents):
        self.documents_by_id = documents

    def documents(self):
        return self

    def get(self, *, documentId):
        if documentId not in self.documents_by_id:
            raise RuntimeError("403 permission denied")
        return FakeRequest(self.documents_by_id[documentId])


def document(text: str, title: str = "Synthetic Article"):
    return {"title": title, "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": text}}]}}]}}


class GoogleDriveTests(unittest.TestCase):
    def adapter(self, values=None, docs=None, *, writeback=False, fail_update=False):
        values = values or [["ID", "Title", "Status", "Doc ID", "Tags", "Ingested", "Hash"], ["TS-EXAMPLE-001", "Example", "Ready", "syntheticDoc_123", "one, two", "", ""]]
        return GoogleDriveAdapter(GoogleDriveConfig("syntheticSheet_123", "Manifest", frozenset({"ready", "updated"}), writeback), FakeSheets(values, fail_update=fail_update), FakeDocs({"syntheticDoc_123": document("Line one\n\nLine two\n")} if docs is None else docs))

    def test_manifest_accepts_optional_columns_and_filters_status(self):
        rows, errors = parse_manifest([["ID", "Doc ID", "Status"], ["one", "syntheticDoc_1", "Ready"], ["two", "syntheticDoc_2", "Draft"]], frozenset({"ready"}))
        self.assertEqual(errors, [])
        self.assertEqual([row["id"] for row in rows], ["one"])

    def test_manifest_reports_malformed_rows_without_aborting(self):
        rows, errors = parse_manifest([["ID", "Doc ID", "Status"], ["", "syntheticDoc_1", "Ready"], ["two", "syntheticDoc_2", "Ready"]], frozenset({"ready"}))
        self.assertEqual([row["id"] for row in rows], ["two"])
        self.assertEqual(errors, ["row=2 code=MALFORMED_ROW"])

    def test_document_extraction_preserves_paragraphs_and_tables(self):
        source = {"body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Heading\n"}}]}}, {"table": {"tableRows": [{"tableCells": [{"content": [{"paragraph": {"elements": [{"textRun": {"content": "Cell\n"}}]}}]}]}]}}]}}
        self.assertEqual(extract_google_doc(source), "Heading\n\nCell\n")

    def test_hash_is_stable_and_repeated_ingestion_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(Path(tmp))
            adapter = self.adapter()
            first = ingest_google(store, adapter, catalog=None)
            second = ingest_google(store, adapter, catalog=None)
            self.assertEqual(first[0]["action"], "NEW")
            self.assertEqual(second[0]["action"], "UNCHANGED")
            self.assertTrue((Path(tmp) / ".fabric" / "google-drive-state.json").exists())

    def test_changed_document_is_update_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(Path(tmp))
            adapter = self.adapter()
            ingest_google(store, adapter)
            changed = self.adapter(docs={"syntheticDoc_123": document("Changed")})
            result = ingest_google(store, changed, dry_run=True)
            self.assertEqual(result[0]["action"], "UPDATE")
            self.assertNotIn("Changed", (Path(tmp) / "normalized" / "ts-example-001.md").read_text())

    def test_missing_document_is_reported_without_logging_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ingest_google(TextStrataStore(Path(tmp)), self.adapter(docs={}), dry_run=True)
            self.assertEqual(result[0]["action"], "ERROR")
            self.assertEqual(result[0]["error"], "DOCUMENT_ACCESS_DENIED")
            self.assertNotIn("Line one", str(result))

    def test_new_ingest_acknowledges_only_three_manifest_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(writeback=True)
            result = ingest_google(TextStrataStore(Path(tmp)), adapter)
            self.assertEqual(result[0]["action"], "NEW + ACKNOWLEDGED")
            updates = adapter.sheets._values.updates[0]["body"]["data"]
            self.assertEqual(len(updates), 3)
            self.assertEqual([entry["range"] for entry in updates], ["'Manifest'!C2", "'Manifest'!F2", "'Manifest'!G2"])

    def test_updated_ingest_is_acknowledged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(Path(tmp))
            ingest_google(store, self.adapter())
            changed = self.adapter(docs={"syntheticDoc_123": document("Changed")}, writeback=True)
            result = ingest_google(store, changed)
            self.assertEqual(result[0]["action"], "UPDATE + ACKNOWLEDGED")

    def test_unchanged_ready_source_retries_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextStrataStore(Path(tmp))
            ingest_google(store, self.adapter())
            adapter = self.adapter(writeback=True)
            result = ingest_google(store, adapter)
            self.assertEqual(result[0]["action"], "UNCHANGED + ACKNOWLEDGED")
            self.assertEqual(len(adapter.sheets._values.updates), 1)

    def test_writeback_failure_keeps_local_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(writeback=True, fail_update=True)
            result = ingest_google(TextStrataStore(Path(tmp)), adapter)
            self.assertEqual(result[0]["action"], "NEW + ACK_ERROR")
            self.assertEqual(result[0]["ack_error"], "ACKNOWLEDGMENT_FAILED")
            self.assertTrue((Path(tmp) / "normalized" / "ts-example-001.md").exists())
            self.assertTrue((Path(tmp) / ".fabric" / "google-drive-state.json").exists())

    def test_local_publication_failure_does_not_acknowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(writeback=True)
            failed = SimpleNamespace(published=False, validation=SimpleNamespace(errors=["synthetic failure"]))
            with patch("textstrata.google_drive.ingest_text", return_value=failed):
                result = ingest_google(TextStrataStore(Path(tmp)), adapter)
            self.assertEqual(result[0]["action"], "ERROR")
            self.assertEqual(adapter.sheets._values.updates, [])

    def test_dry_run_and_disabled_writeback_do_not_update_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(writeback=True)
            result = ingest_google(TextStrataStore(Path(tmp)), adapter, dry_run=True)
            self.assertEqual(result[0]["action"], "NEW + ACK")
            self.assertEqual(adapter.sheets._values.updates, [])
            disabled = self.adapter(writeback=False)
            ingest_google(TextStrataStore(Path(tmp) / "second"), disabled)
            self.assertEqual(disabled.sheets._values.updates, [])

    def test_acknowledgment_rejects_wrong_doc_and_unexpected_status(self):
        adapter = self.adapter(writeback=True)
        record = adapter.discover()[0]
        adapter.sheets._values.values[1][3] = "otherDoc_123"
        with self.assertRaisesRegex(Exception, "ACK_TARGET_MISMATCH"):
            _acknowledge(adapter, record, timestamp="2026-10-03T15:23:41Z", content_hash="a" * 64)
        adapter = self.adapter(writeback=True)
        record = adapter.discover()[0]
        adapter.sheets._values.values[1][2] = "Draft"
        with self.assertRaisesRegex(Exception, "UNEXPECTED_STATUS"):
            _acknowledge(adapter, record, timestamp="2026-10-03T15:23:41Z", content_hash="a" * 64)

    def test_state_directory_can_be_moved_outside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as state_dir:
            with patch.dict("os.environ", {"TEXTSTRATA_STATE_DIR": state_dir}):
                ingest_google(TextStrataStore(Path(tmp)), self.adapter())
            self.assertTrue((Path(state_dir) / "google-drive-state.json").exists())
            self.assertFalse((Path(tmp) / ".fabric" / "google-drive-state.json").exists())

    def test_oauth_scope_error_and_redaction_are_clear(self):
        with self.assertRaisesRegex(Exception, "AUTHENTICATION_SCOPE_MISSING"):
            _validate_oauth_scopes(["https://www.googleapis.com/auth/documents.readonly"])
        self.assertNotIn("secret-value", _redact_error(RuntimeError("access_token=secret-value")))


if __name__ == "__main__":
    unittest.main()
