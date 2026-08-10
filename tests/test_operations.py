import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from textstrata.gateway import GatewayError, CompatibilityGateway
from textstrata.ingest import ingest_text
from textstrata.operations import ARTICLE_ID, ensure_article, get_settings, record_error, save_settings
from textstrata.store import TextStrataStore


def note(body: str) -> str:
    return f"---\nid: note.history\ntitle: History Note\ntags: [history]\n---\n\n# History Note\n\n{body}\n"


class StoreOperationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = TextStrataStore(self.root, revision_limit=3)

    def test_revision_limit_and_restore(self):
        for value in ("one", "two", "three", "four", "five"):
            ingest_text(self.store, note(value))
        revisions = self.store.list_revisions("note.history")
        self.assertEqual(len(revisions), 3)
        self.store.restore_revision("note.history", revisions[-1]["name"])
        current = self.store.normalized_path_for_id("note.history").read_text(encoding="utf-8")
        self.assertIn("two", current)

    def test_trash_restore_and_permanent_purge(self):
        ingest_text(self.store, note("recoverable"))
        deleted = self.store.trash_item("note.history")
        self.assertIsNone(self.store.normalized_path_for_id("note.history"))
        restored = self.store.restore_trash(deleted["trash_name"])
        self.assertEqual(restored, "note.history")
        deleted = self.store.trash_item("note.history")
        self.assertEqual(self.store.purge_trash(deleted["trash_name"]), 1)
        self.assertEqual(self.store.list_trash(), [])

    def test_presentation_settings_persist_and_validate(self):
        saved = save_settings(self.store, {"presentation": {"skin": "wiki", "accent": "blue", "density": "compact", "font_scale": 105, "content_width": "fluid", "card_style": "outlined", "motion": "reduced"}})
        self.assertEqual(saved["presentation"]["skin"], "wiki")
        self.assertEqual(saved["presentation"]["motion"], "reduced")
        self.assertEqual(get_settings(self.store)["presentation"]["font_scale"], 105)
        with self.assertRaises(ValueError):
            save_settings(self.store, {"presentation": {"skin": "neon"}})

    def test_revision_setting_is_bounded_to_one_through_three(self):
        self.assertEqual(save_settings(self.store, {"revision_limit": 2})["revision_limit"], 2)
        with self.assertRaises(ValueError):
            save_settings(self.store, {"revision_limit": 4})

    def test_error_article_updates_observed_block(self):
        ensure_article(self.store)
        record_error(self.store, "upstream-unavailable")
        article = self.store.normalized_path_for_id(ARTICLE_ID).read_text(encoding="utf-8")
        self.assertIn("Error upstream-unavailable", article)
        self.assertIn("| `upstream-unavailable` | 1 |", article)
        self.assertIn("textstrata:observed-errors:start", article)


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == "/api/library":
            payload = {"items": [{"id": "remote-note", "title": "Remote Note", "path": "notes/remote-note", "source_type": "note", "tags": ["remote"], "date_ingested": "2026-07-03"}]}
        elif self.path == "/api/item/notes/remote-note":
            payload = {"metadata": {"title": "Remote Note"}, "markdown": "# Remote Note\n\nImported through the gateway."}
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GatewayTests(unittest.TestCase):
    def test_sync_imports_and_then_skips_unchanged_item(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            store = TextStrataStore(tempfile.mkdtemp())
            gateway = CompatibilityGateway(f"http://127.0.0.1:{server.server_address[1]}")
            first = gateway.sync(store)
            second = gateway.sync(store)
            self.assertEqual(first["imported"], 1)
            self.assertEqual(second["unchanged"], 1)
            self.assertTrue(store.normalized_path_for_id("textstrata.remote-note"))
            with self.assertRaises(GatewayError):
                gateway.request("POST", "/api/not-allowed")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
