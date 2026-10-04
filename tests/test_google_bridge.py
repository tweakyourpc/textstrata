from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textstrata.google_bridge import BridgeClient, BridgeConfig, BridgeError, ingest_bridge, inspect_library_conflict, mirror_bridge, resolve_library_conflict, signed_envelope
from textstrata.store import TextStrataStore


DOC = "syntheticDoc_123"
SOURCE = {"id": "ts-example-001", "doc_id": DOC, "status": "Ready", "title": "Example"}


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.source = {**SOURCE, "content": "# Example\n\nSynthetic content", "tags": ["sample"], "topic": "Test", "origin": "textstrata-inbox"}
        self.ready = [dict(SOURCE)]
        self.library = {}
        self.revisions = {}
        self.fail_ack = False
        self.fail_complete = False

    def call(self, action, payload=None):
        self.calls.append((action, payload))
        if action == "list_ready":
            return {"records": self.ready}
        if action == "fetch_source":
            return {"record": self.source}
        if action == "ack":
            if self.fail_ack:
                raise BridgeError("BRIDGE_OPERATION_FAILED")
            self.ready = []
            return {"status": "Ingested"}
        if action == "mirror_status":
            return {"record": self.library.get(payload["id"])}
        if action == "list_library_status":
            return {"records": list(self.library.values())}
        if action == "list_library_updates":
            return {"records": [{"id": key, "doc_id": value["doc_id"], "hash": value["hash"]} for key, value in self.library.items() if value.get("status") == "Updated"]}
        if action == "fetch_library_revision":
            return {"record": self.revisions[payload["id"]]}
        if action == "fetch_library_conflict":
            if self.library[payload["id"]]["status"] != "Conflict":
                raise BridgeError("LIBRARY_NOT_UPDATED")
            return {"record": {**self.revisions[payload["id"]], "status": "Conflict"}}
        if action == "mark_library_conflict":
            self.library[payload["id"]]["status"] = "Conflict"
            return {"status": "Conflict"}
        if action == "complete_library_import":
            if self.fail_complete:
                raise BridgeError("BRIDGE_UNAVAILABLE")
            self.library[payload["id"]].update({"hash": payload["hash"], "status": "Active"})
            return {"status": "Active"}
        if action == "resolve_library_conflict":
            row = self.library[payload["id"]]
            if row["status"] != "Conflict" or row["hash"] != payload["expected_hash"] or self.revisions[payload["id"]]["fingerprint"] != payload["expected_fingerprint"]:
                raise BridgeError("LIBRARY_REVISION_CHANGED")
            row.update({"hash": payload["hash"], "status": "Active"})
            return {"status": "Active", "hash": payload["hash"]}
        if action == "mirror_upsert":
            self.library[payload["id"]] = {"id": payload["id"], "doc_id": "syntheticLibraryDoc_123", "hash": payload["hash"], "status": "Active"}
            return {"action": "CREATE"}
        raise AssertionError(action)


class BridgeTests(unittest.TestCase):
    def test_signed_vector_and_payload_tampering(self):
        vector = json.loads((Path(__file__).parent / "fixtures" / "google_bridge_vector.json").read_text())
        envelope = signed_envelope(vector["action"], vector["payload"], vector["secret"], timestamp=vector["timestamp"], nonce=vector["nonce"])
        self.assertEqual(envelope["payload_json"], vector["payload_json"])
        self.assertEqual(envelope["signature"], vector["signature"])
        self.assertNotEqual(hashlib.sha256(envelope["payload_json"].replace("sample", "changed").encode()).hexdigest(), vector["signature"])

    def test_client_redacts_transport_errors(self):
        def failing(_url, _body):
            raise BridgeError("BRIDGE_UNAVAILABLE")
        client = BridgeClient(BridgeConfig("https://example.invalid/exec", "synthetic-secret"), transport=failing)
        with self.assertRaisesRegex(BridgeError, "BRIDGE_UNAVAILABLE"):
            client.call("ping")

    def test_large_mirror_write_uses_longer_transport_timeout(self):
        seen = []
        def respond(_request, *, timeout):
            seen.append(timeout)
            return io.BytesIO(b'{"ok":true,"result":{}}')
        with patch("textstrata.google_bridge.urllib.request.urlopen", side_effect=respond):
            BridgeClient._post("https://example.invalid/exec", b'{"action":"ping"}')
            BridgeClient._post("https://example.invalid/exec", b'{"action":"mirror_upsert"}')
        self.assertEqual(seen, [45, 300])

    def test_ingest_ack_retry_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            bridge = FakeBridge()
            bridge.fail_ack = True
            first = ingest_bridge(store, bridge)
            self.assertEqual(first[0]["content"], "NEW")
            self.assertEqual(first[0]["ack"], "BRIDGE_OPERATION_FAILED")
            self.assertEqual(len(store.normalized_paths()), 1)
            bridge.fail_ack = False
            second = ingest_bridge(store, bridge)
            self.assertEqual(second[0]["content"], "UNCHANGED")
            self.assertEqual(second[0]["ack"], "OK")
            self.assertEqual(len(store.normalized_paths()), 1)

    def test_library_origin_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = FakeBridge()
            bridge.source["origin"] = "textstrata-library"
            result = ingest_bridge(TextStrataStore(temp), bridge)
            self.assertEqual(result[0]["error"], "INVALID_SOURCE")
            self.assertFalse(any(action == "ack" for action, _ in bridge.calls))

    def test_mirror_create_update_unchanged_and_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            self.assertEqual(mirror_bridge(store, bridge, dry_run=True)[0]["action"], "CREATE")
            self.assertNotIn("mirror_upsert", [action for action, _ in bridge.calls])
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "CREATE")
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "UNCHANGED")
            path = store.normalized_paths()[0]
            path.write_text(path.read_text() + "\nChanged\n")
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "UPDATE")

    def test_mirror_excludes_configured_canonical_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            with patch.dict("os.environ", {"TEXTSTRATA_MIRROR_EXCLUDE_IDS": "ts-example-001"}):
                self.assertEqual(mirror_bridge(store, bridge, dry_run=True), [])
                self.assertEqual(mirror_bridge(store, bridge), [])
            self.assertTrue(all(action in {"list_library_updates", "list_library_status"} for action, _ in bridge.calls))

    def test_mirror_reads_library_status_once_per_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            mirror_bridge(store, bridge)
            actions = [action for action, _ in bridge.calls]
            self.assertEqual(actions.count("list_library_status"), 1)
            self.assertNotIn("mirror_status", actions)

    def test_mirror_falls_back_for_older_script_deployment(self):
        class LegacyBridge(FakeBridge):
            def call(self, action, payload=None):
                if action == "list_library_status":
                    raise BridgeError("UNKNOWN_ACTION")
                return super().call(action, payload)

        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = LegacyBridge()
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "CREATE")
            self.assertIn("mirror_status", [action for action, _ in bridge.calls])

    def test_library_metadata_import_and_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            mirror_bridge(store, bridge)
            path = store.normalized_paths()[0]
            original = path.read_text()
            base = bridge.library["ts-example-001"]["hash"]
            bridge.library["ts-example-001"]["status"] = "Updated"
            bridge.revisions["ts-example-001"] = {"id": "ts-example-001", "doc_id": "syntheticLibraryDoc_123", "hash": base, "fingerprint": "f" * 64, "content": original, "title": "Revised Example", "topic": "Astronomy", "tags": "sample, jwst", "source": "local"}
            self.assertEqual(mirror_bridge(store, bridge, dry_run=True)[0]["action"], "IMPORT")
            self.assertEqual(path.read_text(), original)
            result = mirror_bridge(store, bridge)
            self.assertEqual(result[0]["action"], "IMPORT")
            self.assertEqual(bridge.library["ts-example-001"]["status"], "Active")
            self.assertIn("Revised Example", path.read_text())
            self.assertIn("jwst", path.read_text())
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "UNCHANGED")
            self.assertEqual(len(store.normalized_paths()), 1)

    def test_library_conflict_does_not_overwrite_local(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            mirror_bridge(store, bridge)
            path = store.normalized_paths()[0]
            base = bridge.library["ts-example-001"]["hash"]
            original = path.read_text()
            path.write_text(original + "\nLocal edit\n")
            bridge.library["ts-example-001"]["status"] = "Updated"
            bridge.revisions["ts-example-001"] = {"id": "ts-example-001", "doc_id": "syntheticLibraryDoc_123", "hash": base, "fingerprint": "f" * 64, "content": original, "title": "Remote edit", "topic": "", "tags": "sample", "source": "local"}
            before = len(bridge.calls)
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "CONFLICT")
            self.assertEqual(bridge.library["ts-example-001"]["status"], "Conflict")
            self.assertIn("Local edit", path.read_text())
            self.assertFalse(any(action in {"mirror_upsert", "complete_library_import"} for action, _ in bridge.calls[before:]))

    def test_explicit_keep_local_preserves_both_copies_before_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            mirror_bridge(store, bridge)
            path = store.normalized_paths()[0]
            baseline = bridge.library["ts-example-001"]["hash"]
            original = path.read_text()
            path.write_text(original + "\nLocal edit\n")
            bridge.library["ts-example-001"]["status"] = "Conflict"
            bridge.revisions["ts-example-001"] = {"id": "ts-example-001", "doc_id": "syntheticLibraryDoc_123", "hash": baseline, "fingerprint": "f" * 64, "content": original, "title": "Remote title", "topic": "", "tags": "sample", "source": "local"}
            inspected = inspect_library_conflict(store, bridge, "ts-example-001")
            result = resolve_library_conflict(store, bridge, "ts-example-001", resolution="keep_local", expected_local_sha256=inspected["local_sha256"], expected_google_fingerprint=inspected["google_fingerprint"], reason="local change wins after review")
            self.assertEqual(result["status"], "Active")
            self.assertTrue(Path(result["snapshot"] + ".local.md").exists())
            self.assertTrue(Path(result["snapshot"] + ".google.md").exists())
            self.assertIn("Local edit", path.read_text())
            self.assertEqual(bridge.library["ts-example-001"]["status"], "Active")

    def test_explicit_keep_google_and_stale_local_precondition(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            mirror_bridge(store, bridge)
            path = store.normalized_paths()[0]
            baseline = bridge.library["ts-example-001"]["hash"]
            original = path.read_text()
            path.write_text(original + "\nLocal edit\n")
            bridge.library["ts-example-001"]["status"] = "Conflict"
            bridge.revisions["ts-example-001"] = {"id": "ts-example-001", "doc_id": "syntheticLibraryDoc_123", "hash": baseline, "fingerprint": "f" * 64, "content": original, "title": "Remote title", "topic": "", "tags": "sample", "source": "local"}
            inspected = inspect_library_conflict(store, bridge, "ts-example-001")
            path.write_text(path.read_text() + "\nNewer local edit\n")
            with self.assertRaisesRegex(BridgeError, "LIBRARY_REVISION_CHANGED"):
                resolve_library_conflict(store, bridge, "ts-example-001", resolution="keep_google", expected_local_sha256=inspected["local_sha256"], expected_google_fingerprint=inspected["google_fingerprint"], reason="reviewed Google copy")
            self.assertEqual(bridge.library["ts-example-001"]["status"], "Conflict")
            current = inspect_library_conflict(store, bridge, "ts-example-001")
            result = resolve_library_conflict(store, bridge, "ts-example-001", resolution="keep_google", expected_local_sha256=current["local_sha256"], expected_google_fingerprint=current["google_fingerprint"], reason="reviewed Google copy")
            self.assertEqual(result["status"], "Active")
            self.assertIn("Remote title", path.read_text())

    def test_library_import_completion_retries_without_republishing(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            ingest_bridge(store, FakeBridge())
            bridge = FakeBridge()
            mirror_bridge(store, bridge)
            path = store.normalized_paths()[0]
            original = path.read_text()
            base = bridge.library["ts-example-001"]["hash"]
            bridge.library["ts-example-001"]["status"] = "Updated"
            bridge.revisions["ts-example-001"] = {"id": "ts-example-001", "doc_id": "syntheticLibraryDoc_123", "hash": base, "fingerprint": "f" * 64, "content": original, "title": "Retry Example", "topic": "", "tags": "sample", "source": "local"}
            bridge.fail_complete = True
            self.assertEqual(mirror_bridge(store, bridge)[0]["error"], "BRIDGE_UNAVAILABLE")
            updated = path.read_text()
            self.assertIn("Retry Example", updated)
            bridge.fail_complete = False
            self.assertEqual(mirror_bridge(store, bridge)[0]["action"], "RETRY")
            self.assertEqual(path.read_text(), updated)
            self.assertEqual(bridge.library["ts-example-001"]["status"], "Active")

    def test_dry_run_does_not_publish_or_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TextStrataStore(temp)
            bridge = FakeBridge()
            result = ingest_bridge(store, bridge, dry_run=True)
            self.assertEqual(result[0]["action"], "NEW + ACK")
            self.assertFalse(store.normalized_paths())
            self.assertNotIn("ack", [action for action, _ in bridge.calls])


if __name__ == "__main__":
    unittest.main()
