"""Tests for the web server (M2).

Covers all route handlers with a temp store. Server starts on a random port
and is torn down after each test.
"""

import http.client
import json
import os
import shutil
import subprocess
import socket
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp

from textstrata import __version__
from textstrata.web import TextStrataWebApp, create_handler

TEST_ITEM = """---
id: test.hello
title: Hello World
type: note
tags: [test, hello]
handling: human_plus_ai
preservation: preserve_exact
---

# Hello

This is a test note.
"""

TEST_ITEM_2 = """---
id: test.foo
title: Foo Bar
type: reference
tags: [test, foo]
handling: human_plus_ai
preservation: preserve_exact
---

# Foo

This is about foo.
"""

TEST_YOUTUBE_TRANSCRIPT = """---
id: test.video
title: Caption Video
type: reference
tags: [youtube, transcript]
source_url: https://www.youtube.com/watch?v=fixture
---

# Caption Video

## Timestamped transcript

[00:01.250] First caption.
[00:04.000] Final caption.
"""


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(mkdtemp())
        os.environ["MARKBASE_WORKSPACE"] = str(self.tmp)
        self.app = TextStrataWebApp(workspace_root=self.tmp)
        self.handler = create_handler(self.app)
        self.port = _free_port()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self.handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()

    def _get(self, path: str) -> tuple[int, str, dict]:
        try:
            resp = urllib.request.urlopen(f"{self.base}{path}", timeout=5)
            body = resp.read().decode("utf-8")
            return resp.status, body, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return exc.code, body, dict(exc.headers)

    def _dump_dom(self, path: str, width: int = 1280) -> str:
        chrome = shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome:
            self.skipTest("Chromium is not installed")
        profile = self.tmp / f"chrome-{width}"
        result = subprocess.run(
            [chrome, "--headless", "--no-sandbox", "--disable-gpu",
             f"--user-data-dir={profile}", f"--window-size={width},844",
             "--virtual-time-budget=3000", "--dump-dom", f"{self.base}{path}"],
            capture_output=True, text=True, timeout=25, check=True,
        )
        return result.stdout

    def _post(self, path: str, data: bytes | None = None,
              ct: str = "application/json",
              headers: dict | None = None) -> tuple[int, str, dict]:
        hdrs = {"Content-Type": ct, **(headers or {})}
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=hdrs, method="POST"
        )
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            body = resp.read().decode("utf-8")
            return resp.status, body, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return exc.code, body, dict(exc.headers)

    def _delete(self, path: str, headers: dict | None = None) -> tuple[int, str, dict]:
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(
            f"{self.base}{path}", data=b"", headers=hdrs, method="DELETE"
        )
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            body = resp.read().decode("utf-8")
            return resp.status, body, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return exc.code, body, dict(exc.headers)

    def _ingest(self, content: str):
        from textstrata.ingest import ingest_text
        return ingest_text(self.app.store, content, fallback_id="test")

    # --- GET routes ---

    def test_get_whoami(self):
        status, body, _ = self._get("/whoami")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["service"], "textstrata")
        self.assertEqual(data["version"], __version__)
        self.assertEqual(self.handler.server_version, f"TextStrata/{__version__}")

    def test_get_home(self):
        status, body, headers = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn('<meta charset="utf-8">', body)

    def test_get_versioned_client_assets(self):
        for name, marker in (("library", "openImportHistory"), ("new-note", "activateSource")):
            path = f"/static/textstrata-{name}-{__version__}.js"
            status, body, headers = self._get(path)
            self.assertEqual(status, 200)
            self.assertIn(marker, body)
            self.assertEqual(headers["Cache-Control"], "public, max-age=31536000, immutable")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_get_stale_client_asset_not_found(self):
        status, _, _ = self._get("/static/textstrata-library-0.0.0.js")
        self.assertEqual(status, 404)

    def test_browser_executes_saved_view_and_dialog_deep_links(self):
        recent = self._dump_dom("/recent")
        self.assertRegex(recent, r'data-workspace-view="recent"[^>]*aria-current="page"')
        settings = self._dump_dom("/?open=settings")
        self.assertRegex(settings, r'<dialog id="settings-dialog"[^>]*open=""')

    def test_browser_executes_new_note_asset_at_mobile_width(self):
        mobile = self._dump_dom("/new", width=390)
        self.assertIn('id="ingest-queue"', mobile)
        self.assertNotIn('Loading recent imports...', mobile)

    def test_get_home_with_items(self):
        self._ingest(TEST_ITEM)
        status, body, headers = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Hello World", body)

    def test_get_item_text_view_uses_utf8_charset(self):
        self._ingest(TEST_ITEM)
        status, body, headers = self._get("/item/test.hello?format=text")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("ID: test.hello", body)

    def test_get_stable_corpus_views(self):
        self._ingest(TEST_ITEM)
        untagged = TEST_ITEM_2.replace("id: test.foo", "id: test.untagged").replace("tags: [test, foo]", "tags: []")
        self._ingest(untagged)
        for path, title in (("/recent", "Recent"), ("/needs-curation", "Needs curation"), ("/untagged", "Untagged")):
            status, body, _ = self._get(path)
            self.assertEqual(status, 200)
            self.assertIn(f"<title>{title} - TextStrata</title>", body)
            self.assertIn(f'data-workspace-view="{path[1:]}"', body)
        _, untagged_body, _ = self._get("/untagged")
        self.assertIn("test.untagged", untagged_body)
        self.assertNotIn('id="item-test.hello"', untagged_body)

    def test_get_search_no_query(self):
        status, body, _ = self._get("/search")
        self.assertEqual(status, 200)
        self.assertIn("Search", body)

    def test_get_search_with_results(self):
        self._ingest(TEST_ITEM)
        status, body, _ = self._get("/search?q=hello")
        self.assertEqual(status, 200)
        self.assertIn("Hello World", body)

    def test_get_search_empty_results(self):
        status, body, _ = self._get("/search?q=zzzzznonexistent")
        self.assertEqual(status, 200)

    def test_get_item_found(self):
        self._ingest(TEST_ITEM)
        status, body, _ = self._get("/item/test.hello")
        self.assertEqual(status, 200)
        self.assertIn("Hello World", body)

    def test_get_item_not_found(self):
        status, body, _ = self._get("/item/nonexistent")
        self.assertEqual(status, 404)

    def test_get_youtube_caption_exports(self):
        self._ingest(TEST_YOUTUBE_TRANSCRIPT)

        status, body, headers = self._get("/api/notes/test.video/export/vtt")
        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            "WEBVTT\n\n"
            "00:00:01.250 --> 00:00:04.000\n"
            "First caption.\n\n"
            "00:00:04.000 --> 00:00:07.000\n"
            "Final caption.\n",
        )
        self.assertEqual(headers["Content-Type"], "text/vtt; charset=utf-8")
        self.assertEqual(
            headers["Content-Disposition"],
            "attachment; filename=\"test.video.vtt\"",
        )
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

        status, body, headers = self._get("/api/notes/test.video/export/srt")
        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            "1\n"
            "00:00:01,250 --> 00:00:04,000\n"
            "First caption.\n\n"
            "2\n"
            "00:00:04,000 --> 00:00:07,000\n"
            "Final caption.\n",
        )
        self.assertEqual(
            headers["Content-Type"],
            "application/x-subrip; charset=utf-8",
        )
        self.assertEqual(
            headers["Content-Disposition"],
            "attachment; filename=\"test.video.srt\"",
        )

    def test_caption_export_rejects_non_transcript_note(self):
        self._ingest(TEST_ITEM)
        status, body, _ = self._get("/api/notes/test.hello/export/vtt")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["code"], "caption-export-not-found")

    def test_get_tag(self):
        self._ingest(TEST_ITEM)
        status, body, _ = self._get("/tag/hello")
        self.assertEqual(status, 200)
        self.assertIn("Hello World", body)

    def test_get_tag_no_results(self):
        status, body, _ = self._get("/tag/nonexistent")
        self.assertEqual(status, 200)

    def test_get_system_info(self):
        status, body, _ = self._get("/api/textstrata/system-info")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("version", data)
        self.assertIn("pid", data)

    def test_legacy_fabric_api_prefix_remains_supported(self):
        status, body, _ = self._get("/api/fabric/system-info")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], __version__)

    def test_get_settings(self):
        status, body, _ = self._get("/api/textstrata/settings")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("revision_limit", data)

    def test_get_asset_not_found(self):
        status, body, _ = self._get("/asset/nonexistent")
        self.assertEqual(status, 404)

    def test_get_graph(self):
        status, body, _ = self._get("/graph")
        self.assertEqual(status, 200)
        # Returns the D3 HTML page
        self.assertIn("d3.v7.min.js", body)

    def test_get_new_note_workspace(self):
        status, body, _ = self._get("/new")
        self.assertEqual(status, 200)
        self.assertIn("New Note - TextStrata", body)
        self.assertIn('role="tablist"', body)
        self.assertNotIn('id="ingest-panel"', body)

    def test_legacy_new_note_panel_url_redirects(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/?panel=new")
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/new")
        connection.close()

    def test_get_api_graph(self):
        status, body, _ = self._get("/api/textstrata/graph")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("nodes", data)
        self.assertIn("links", data)
        self.assertIn("similarity", data)

    def test_get_review_empty(self):
        status, body, _ = self._get("/api/textstrata/review")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 0)

    def test_get_trash_empty(self):
        status, body, _ = self._get("/api/textstrata/trash")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["items"], [])

    def test_get_revisions_not_found(self):
        status, body, _ = self._get("/api/textstrata/item/nonexistent/revisions")
        self.assertEqual(status, 200)

    def test_get_unknown_route_returns_404(self):
        status, body, _ = self._get("/nonexistent")
        self.assertEqual(status, 404)

    # --- POST /ingest ---

    def test_post_ingest_json(self):
        payload = json.dumps({"content": TEST_ITEM}).encode("utf-8")
        status, body, _ = self._post("/ingest", data=payload)
        self.assertEqual(status, 201)
        data = json.loads(body)
        self.assertEqual(data["item_id"], "test.hello")

    def test_post_ingest_empty_rejected(self):
        payload = json.dumps({"content": ""}).encode("utf-8")
        status, body, _ = self._post("/ingest", data=payload)
        self.assertEqual(status, 400)

    def test_post_ingest_invalid_rejected(self):
        bad = "---\nid: Bad Id\ntitle: \n---\n"
        payload = json.dumps({"content": bad}).encode("utf-8")
        status, body, _ = self._post("/ingest", data=payload)
        self.assertEqual(status, 422)

    # --- POST /api/textstrata/ingest (same-origin bypass) ---
    def test_post_fabric_ingest_json(self):
        payload = json.dumps({"content": TEST_ITEM}).encode("utf-8")
        status, body, _ = self._post("/api/textstrata/ingest", data=payload)
        self.assertEqual(status, 404)

    # --- POST /api/asset/upload ---
    def test_post_asset_upload_no_multipart_rejected(self):
        status, body, _ = self._post("/api/asset/upload",
                                     data=b"not multipart",
                                     ct="text/plain")
        self.assertEqual(status, 400)

    # --- POST /api/textstrata/settings ---
    def test_post_settings(self):
        payload = json.dumps({"revision_limit": 2}).encode("utf-8")
        status, body, _ = self._post("/api/textstrata/settings", data=payload)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["revision_limit"], 2)

    # --- POST /api/textstrata/item/<id>/save ---
    def test_post_item_save(self):
        self._ingest(TEST_ITEM)
        new_body = json.dumps({"content": "Updated content"}).encode("utf-8")
        status, body, _ = self._post("/api/textstrata/item/test.hello/save",
                                     data=new_body)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["saved"])

    def test_post_item_save_empty_rejected(self):
        self._ingest(TEST_ITEM)
        status, body, _ = self._post("/api/textstrata/item/test.hello/save",
                                     data=b"{}")
        self.assertEqual(status, 400)

    # --- POST /api/textstrata/restart ---
    def test_post_restart(self):
        hdrs = {"X-TextStrata-Confirm": "true"}
        status, body, _ = self._post("/api/textstrata/restart", headers=hdrs)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["restarting"])

    # --- DELETE /api/textstrata/item/<id>/trash ---
    def test_trash_item(self):
        self._ingest(TEST_ITEM)
        hdrs = {"X-TextStrata-Confirm": "true"}
        status, body, _ = self._post("/api/textstrata/item/test.hello/trash",
                                     data=b"{}", headers=hdrs)
        self.assertEqual(status, 200)
        # Verify it's gone from the catalog
        status, body, _ = self._get("/item/test.hello")
        self.assertEqual(status, 404)

    def test_trash_without_confirmation_rejected(self):
        self._ingest(TEST_ITEM)
        status, body, _ = self._post("/api/textstrata/item/test.hello/trash",
                                     data=b"{}")
        self.assertEqual(status, 409)

    # --- acquisition queue/capabilities ---
    def test_get_capabilities(self):
        status, body, _ = self._get("/api/acquisition/capabilities")
        self.assertEqual(status, 200)

    def test_get_queue(self):
        status, body, _ = self._get("/api/acquisition/queue")
        self.assertEqual(status, 200)

    # --- 404 for unknown API routes ---
    def test_unknown_api_route(self):
        status, _, _ = self._get("/api/unknown")
        self.assertEqual(status, 404)

    def test_unknown_post_route(self):
        status, _, _ = self._post("/api/unknown")
        self.assertEqual(status, 404)


class CatalogTests(unittest.TestCase):
    """Tests for catalog.py incremental methods (M3)."""

    def setUp(self):
        self.tmp = Path(mkdtemp())
        from textstrata.catalog import Catalog
        self.cat = Catalog(workspace_root=self.tmp)

    def tearDown(self):
        self.cat.close()

    def _make_item(self, item_id="a.b", title="T", body="body",
                   tags=None, type_str="note"):
        from textstrata.models import ContentType, TextStrataItem, HandlingMode, PreservationMode
        return TextStrataItem(
            id=item_id,
            title=title,
            type=ContentType.coerce(type_str),
            tags=tags or [],
            body=body,
            handling=HandlingMode.HUMAN_PLUS_AI,
            preservation=PreservationMode.PRESERVE_EXACT,
        )

    def test_index_and_search(self):
        item = self._make_item()
        self.cat.index_item(item)
        hits = self.cat.search("body")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].id, "a.b")

    def test_remove_item(self):
        item = self._make_item()
        self.cat.index_item(item)
        self.cat.remove_item("a.b")
        hits = self.cat.search("body")
        self.assertEqual(len(hits), 0)

    def test_remove_nonexistent(self):
        self.cat.remove_item("nonexistent")
        self.assertEqual(self.cat.count(), 0)

    def test_list_items(self):
        items = [self._make_item("a.b", "A"), self._make_item("c.d", "C")]
        for it in items:
            self.cat.index_item(it)
        listed = self.cat.list_items()
        self.assertEqual(len(listed), 2)
        ids = {r.id for r in listed}
        self.assertIn("a.b", ids)
        self.assertIn("c.d", ids)

    def test_list_items_empty(self):
        self.assertEqual(self.cat.list_items(), [])

    def test_update_scores(self):
        from textstrata.similarity import KnowledgeScore
        item = self._make_item()
        self.cat.index_item(item)
        scores = {"a.b": KnowledgeScore("a.b", 0.85, 0.1, 0.1, 0.1, 3, "main", [])}
        self.cat.update_scores(scores)
        hits = self.cat.search("body", sort="score")
        self.assertGreater(hits[0].knowledge_score, 0.8)


class StoreTests(unittest.TestCase):
    """Tests for TextStrataStore (M3)."""

    def setUp(self):
        self.tmp = Path(mkdtemp())
        from textstrata.store import TextStrataStore
        self.store = TextStrataStore(workspace_root=self.tmp)
        self.store.ensure_dirs()
        from textstrata.models import TextStrataItem, ContentType, HandlingMode, PreservationMode
        self.item = TextStrataItem(
            id="test.store",
            title="Store Test",
            type=ContentType.NOTE,
            tags=["test"],
            body="# Store Test\n\nBody here.",
            handling=HandlingMode.HUMAN_PLUS_AI,
            preservation=PreservationMode.PRESERVE_EXACT,
        )

    def test_publish_and_read_normalized(self):
        self.store.publish_normalized(self.item)
        paths = self.store.normalized_paths()
        self.assertTrue(any(p.name == "test.store.md" for p in paths))

    def test_save_original(self):
        self.store.save_original("test.store", "# raw\n")
        path = self.store.original_dir / "test.store.md"
        self.assertTrue(path.exists())

    def test_trash_and_restore(self):
        self.store.publish_normalized(self.item)
        result = self.store.trash_item("test.store")
        self.assertIn("trash_name", result)
        self.assertIsNone(self.store.normalized_path_for_id("test.store"))
        restored = self.store.restore_trash(result["trash_name"])
        self.assertEqual(restored, "test.store")
        self.assertIsNotNone(self.store.normalized_path_for_id("test.store"))

    def test_trash_nonexistent_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.store.trash_item("nonexistent")

    def test_list_trash_empty(self):
        self.assertEqual(self.store.list_trash(), [])

    def test_purge_trash(self):
        self.store.publish_normalized(self.item)
        self.store.trash_item("test.store")
        n = self.store.purge_trash()
        self.assertGreater(n, 0)
        self.assertEqual(self.store.list_trash(), [])

    def test_revision_limit(self):
        self.store.publish_normalized(self.item)
        for i in range(5):
            self.item.body = f"# Revision {i}"
            self.store.publish_normalized(self.item)
        revs = self.store.list_revisions("test.store")
        self.assertLessEqual(len(revs), 3)


if __name__ == "__main__":
    unittest.main()
