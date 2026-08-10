import json
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from threading import Thread

from textstrata.ingest import ingest_text
from textstrata.presentation import PAPER_SKIN, render_library_index
from textstrata.presentation.browser_assets import client_asset_content
from textstrata.web import TextStrataWebApp, create_handler


NOTE = """---
id: note.operations
title: Operations Note
type: reference
---

# Operations Note

Temporary content for an isolated operational-route test.
"""


class OperationalDialogPresentationTests(unittest.TestCase):
    def test_library_separates_preferences_from_operational_dialogs(self):
        html = render_library_index([], PAPER_SKIN)
        script = client_asset_content("library", "test", "test")

        for dialog_id in (
            "settings-dialog",
            "review-dialog",
            "trash-dialog",
            "sync-dialog",
            "maintenance-dialog",
            "confirm-dialog",
        ):
            self.assertEqual(html.count(f'id="{dialog_id}"'), 1, dialog_id)

        settings = re.search(
            r'<dialog id="settings-dialog".*?</dialog>', html, flags=re.S
        ).group(0)
        self.assertIn("Appearance", settings)
        self.assertIn("Library", settings)
        self.assertNotIn("Trash", settings)
        self.assertNotIn("Maintenance", settings)

        self.assertIn('option value="never"', html)
        self.assertNotIn('option value="keep"', html)
        for path in (
            "/api/acquisition/queue/clear-completed",
            "/api/acquisition/maintenance/settings",
            "/api/acquisition/maintenance/restart",
            "/api/acquisition/channel/",
            "/api/textstrata/trash",
            "/api/textstrata/restart",
        ):
            self.assertIn(path, script)

        for target in ("settings", "review", "trash", "imports", "maintenance"):
            self.assertIn(f'openTarget === "{target}"', script)


class OperationalRouteTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = TextStrataWebApp(self.root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.app))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()

    def request(self, method, path, payload=None, *, confirm=False):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if confirm:
            headers["X-TextStrata-Confirm"] = "true"
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
            return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_retention_requires_confirmation_and_persists_never_mode(self):
        payload = {
            "retain_original_uploads_default": True,
            "retained_originals_purge_mode": "never",
            "retained_originals_days": 45,
        }
        status, body = self.request(
            "POST", "/api/acquisition/maintenance/settings", payload
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "confirmation-required")

        status, _ = self.request(
            "POST", "/api/acquisition/maintenance/settings", payload, confirm=True
        )
        self.assertEqual(status, 200)
        status, body = self.request("GET", "/api/acquisition/maintenance/settings")
        self.assertEqual(status, 200)
        self.assertEqual(body["retained_originals_purge_mode"], "never")
        self.assertEqual(body["retained_originals_days"], 45)

    def test_trash_can_be_listed_and_restored_through_http(self):
        ingest_text(self.app.store, NOTE)
        status, trashed = self.request(
            "POST", "/api/textstrata/item/note.operations/trash", {}, confirm=True
        )
        self.assertEqual(status, 200)

        status, body = self.request("GET", "/api/textstrata/trash")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["item_id"], "note.operations")

        name = trashed["trash_name"]
        status, body = self.request(
            "POST", f"/api/textstrata/trash/{name}/restore", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["restored"], "note.operations")
        status, body = self.request("GET", "/api/textstrata/trash")
        self.assertEqual(body["items"], [])


if __name__ == "__main__":
    unittest.main()
