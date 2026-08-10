import http.client
import json
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from textstrata.acquisition import _extract_html_source_date, _format_transcript_stamp, _normalize_source_date
from textstrata.application.acquisition import acquisition_maintenance_settings_payload, acquisition_queue_payload, build_ingest_submission, clear_acquisition_completed, save_acquisition_maintenance_settings
from textstrata.application.library import reason_labels, retrieval_labels
from textstrata.application.settings import build_system_info_payload, load_settings_payload, save_settings_payload
from textstrata.presentation.browser_assets import client_asset_content
from textstrata.presentation.client_scripts import item_page_script, library_page_script
from textstrata.presentation.new_note_client import new_note_page_script
from textstrata.ingest import build_item, ingest_text
from textstrata.presentation import PAPER_SKIN, RenderContext, render_item_html, render_library_index, render_new_note_html, render_text
from textstrata.presentation.markdown import calculate_read_time
from textstrata.store import TextStrataStore
from textstrata.web import TextStrataWebApp, create_handler
from http.server import ThreadingHTTPServer


NOTE = """---
id: note.frontend
title: Frontend Notes
type: reference
tags: [ui, accessibility]
---

# Frontend Notes

- preserve semantics
- keep keyboard navigation first-class

Use `aria-label` where needed.
"""


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)

    def test_render_text_includes_metadata(self):
        res = ingest_text(self.store, NOTE)
        ctx = RenderContext(title=res.item.title, item=res.item, validation_errors=[], validation_warnings=[], suggested_tags=["accessibility"])
        text = render_text(ctx)
        self.assertIn("ID: note.frontend", text)
        self.assertIn("Suggested tags: accessibility", text)
        self.assertIn("Body", text)

    def test_calculate_read_time_ignores_frontmatter_code_blocks_and_html(self):
        prose = " ".join(f"word{i}" for i in range(500))
        markdown = "---\nid: note.read-time\ntitle: Read Time\n---\n\n```python\nprint(\"ignore me\")\n```\n\n<div>ignore this tag</div>\n\n" + prose
        self.assertEqual(calculate_read_time(markdown), 2)

    def test_render_html_has_semantic_structure(self):
        res = ingest_text(self.store, NOTE)
        ctx = RenderContext(title=res.item.title, item=res.item, validation_errors=[], validation_warnings=[], suggested_tags=["accessibility"], raw_markdown=self.store.normalized_path_for_id(res.item.id).read_text(encoding="utf-8"))
        html = render_item_html(ctx, PAPER_SKIN)
        self.assertIn('<main id="content">', html)
        self.assertIn('<article class="article">', html)
        self.assertIn('<aside class="side-panel">', html)
        self.assertIn('<h1 class="title">Frontend Notes</h1>', html)
        self.assertIn('min read</span>', html)
        self.assertIn("<code>aria-label</code>", html)
        self.assertIn('id="edit-textarea"', html)

    def test_render_html_deep_links_library_operations_and_keeps_delete_action(self):
        res = ingest_text(self.store, NOTE)
        ctx = RenderContext(title=res.item.title, item=res.item, validation_errors=[], validation_warnings=[], suggested_tags=["accessibility"], raw_markdown=self.store.normalized_path_for_id(res.item.id).read_text(encoding="utf-8"))
        html = render_item_html(ctx, PAPER_SKIN)
        self.assertNotIn('id="settings-dialog"', html)
        self.assertNotIn('id="sync-dialog"', html)
        self.assertNotIn('id="confirm-dialog"', html)
        self.assertIn('/?open=settings', html)
        self.assertIn('/?open=review', html)
        self.assertIn('/?open=trash', html)
        self.assertIn('/?open=imports', html)
        self.assertIn('/?open=maintenance', html)
        self.assertIn('window.location.href="/new"', html)
        self.assertIn('data-trash-item="note.frontend"', html)
        self.assertIn('/item/system.operations-error-reference', html)
        self.assertIn('raState="paused"', html)
        self.assertIn('window.speechSynthesis.cancel();raPp.textContent="Resume"', html)
        self.assertIn('raState="playing";raSession++;raSpeakCurrent()', html)
        self.assertIn('if(raStop)raStop.onclick=raReset', html)
        self.assertIn('window.speechSynthesis.cancel();raSpeakCurrent()', html)
        self.assertNotIn('window.speechSynthesis.pause()', html)
        self.assertIn('if(d&&!d.open)d.showModal()', html)
        self.assertIn('aboutTitle.textContent = a===\"about\" ? \"About\" : \"System info\"', html)
        self.assertNotIn('/api/acquisition/maintenance/settings', html)

    def test_keyboard_shortcuts_do_not_capture_f(self):
        res = ingest_text(self.store, NOTE)
        ctx = RenderContext(title=res.item.title, item=res.item, validation_errors=[], validation_warnings=[], suggested_tags=[], raw_markdown=self.store.normalized_path_for_id(res.item.id).read_text(encoding="utf-8"))
        item_html = render_item_html(ctx, PAPER_SKIN)
        library_html = render_library_index([res.item], PAPER_SKIN)
        self.assertNotIn('key === "f"', item_html)
        self.assertNotIn('key===\"f\"', library_html)
        self.assertNotIn('<span class="kbd">F</span>', item_html)
        self.assertNotIn('<span class="kbd">F</span>', library_html)

    def test_rendered_pages_have_unique_dialog_ids(self):
        res = ingest_text(self.store, NOTE)
        ctx = RenderContext(title=res.item.title, item=res.item, validation_errors=[], validation_warnings=[], suggested_tags=[], raw_markdown=self.store.normalized_path_for_id(res.item.id).read_text(encoding="utf-8"))
        for html in (render_item_html(ctx, PAPER_SKIN), render_library_index([res.item], PAPER_SKIN), render_new_note_html(PAPER_SKIN)):
            ids = re.findall(r'id="([^"]+)"', html)
            self.assertEqual(len(ids), len(set(ids)), sorted(i for i in set(ids) if ids.count(i) > 1))

    def test_rendered_inline_scripts_pass_node_check(self):
        res = ingest_text(self.store, NOTE)
        ctx = RenderContext(title=res.item.title, item=res.item, validation_errors=[], validation_warnings=[], suggested_tags=[], raw_markdown=self.store.normalized_path_for_id(res.item.id).read_text(encoding="utf-8"))
        item_html = render_item_html(ctx, PAPER_SKIN)
        scripts = re.findall(r"<script>(.*?)</script>", item_html, flags=re.S)
        scripts.extend([
            client_asset_content("library", "test", "test"),
            client_asset_content("new-note", "test", "test"),
        ])
        self.assertTrue(scripts)
        for idx, script in enumerate(scripts):
            script_path = Path(self.tmp) / f"script-{idx}.js"
            script_path.write_text(script, encoding="utf-8")
            proc = subprocess.run(["node", "--check", str(script_path)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


    def test_client_scripts_are_owned_outside_page_renderers(self):
        item_script = item_page_script("note.frontend")
        library_script = library_page_script()
        new_note_script = new_note_page_script()
        self.assertIn("<script>", item_script)
        self.assertIn("/api/textstrata/item/note.frontend/save", item_script)
        self.assertIn('raState="paused"', item_script)
        self.assertNotIn("window.speechSynthesis.pause()", item_script)
        self.assertIn("async function loadSettings()", library_script)
        self.assertIn("JSON.stringify({presentation})", library_script)
        self.assertIn('function activateSource(source', new_note_script)
        self.assertIn('tabs[next].focus()', new_note_script)
        self.assertIn('/api/acquisition/ingest', new_note_script)
        self.assertIn('/api/asset/upload', new_note_script)

    def test_library_reason_labels_are_application_level(self):
        res = ingest_text(self.store, NOTE)
        labels = reason_labels(res.item, "frontend accessibility")
        self.assertIn("title match", labels)
        self.assertIn("tag match", labels)
        self.assertIn("full text", reason_labels(res.item, "navigation"))

    def test_retrieval_labels_explain_sort_metadata(self):
        res = ingest_text(self.store, NOTE)
        hit = type("Hit", (), {"knowledge_score": 0.81234, "ingested_at": "2026-07-22T00:00:00Z", "snippet": "[frontend] match"})()
        self.assertEqual(retrieval_labels(res.item, "frontend", hit, "score")[0], "graph importance 0.812")
        self.assertEqual(retrieval_labels(res.item, "frontend", hit, "newest")[0], "indexed 2026-07-22")

    def test_render_html_embeds_asset_preview_images(self):
        raw = """---
id: note.asset
title: Asset Note
type: reference
---
# Asset Note

![Diagram](/asset/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?preview=1)
"""
        result = ingest_text(self.store, raw)
        ctx = RenderContext(title=result.item.title, item=result.item, validation_errors=[], validation_warnings=[], suggested_tags=[])
        html = render_item_html(ctx, PAPER_SKIN)
        self.assertIn('class="content-image"', html)
        self.assertIn('/asset/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?preview=1', html)

    def test_render_html_shows_original_ingested_and_last_edited_dates(self):
        raw = """---
id: note.dates
title: Date Note
type: reference
document_date: 2024-01-02T03:04:05+00:00
extra:
  last_edited_at: 2026-07-04T15:20:00+00:00
provenance:
  ingested_at: 2026-07-04T15:10:00+00:00
---
# Date Note
"""
        result = ingest_text(self.store, raw)
        ctx = RenderContext(title=result.item.title, item=result.item, validation_errors=[], validation_warnings=[], suggested_tags=[])
        html = render_item_html(ctx, PAPER_SKIN)
        self.assertIn('Original date</th><td>2024-01-02 03:04:05 UTC', html)
        self.assertIn('Ingested</th><td>2026-07-04 15:10:00 UTC', html)
        self.assertIn('Last edited</th><td>2026-07-04 15:20:00 UTC', html)

    def test_youtube_timestamp_rows_link_back_to_video(self):
        raw = """---
id: note.video
title: Video Note
type: reference
source_url: https://www.youtube.com/watch?v=abc123
---
# Video Note

## Timestamped transcript

[00:01:02] Linked transcript text.
"""
        result = ingest_text(self.store, raw)
        ctx = RenderContext(title=result.item.title, item=result.item, validation_errors=[], validation_warnings=[], suggested_tags=[])
        html = render_item_html(ctx, PAPER_SKIN)
        self.assertIn("https://www.youtube.com/watch?v=abc123&amp;t=62s", html)
        self.assertIn('class="transcript-time"', html)
        self.assertIn('target="_blank" rel="noopener"', html)
        self.assertIn("Export captions", html)
        self.assertIn("/api/notes/note.video/export/vtt", html)
        self.assertIn("/api/notes/note.video/export/srt", html)

    def test_caption_export_menu_is_hidden_for_regular_notes(self):
        result = ingest_text(self.store, NOTE)
        ctx = RenderContext(
            title=result.item.title,
            item=result.item,
            validation_errors=[],
            validation_warnings=[],
            suggested_tags=[],
        )
        html = render_item_html(ctx, PAPER_SKIN)
        self.assertNotIn("Export captions", html)
        self.assertNotIn("/export/vtt", html)
        self.assertNotIn("/export/srt", html)

    def test_render_library_index(self):
        res = ingest_text(self.store, NOTE)
        html = render_library_index([res.item], PAPER_SKIN)
        self.assertIn("note.frontend", html)
        self.assertIn("Frontend Notes", html)
        self.assertIn("/item/note.frontend", html)
        self.assertIn('id="item-note.frontend"', html)
        self.assertIn('data-search="frontend notes note.frontend reference ui accessibility', html)
        self.assertIn("1 note", html)
        self.assertIn("Search titles, tags, IDs, and full text", html)
        self.assertIn('href="/tag/accessibility"', html)
        self.assertIn("New note", html)
        self.assertIn("design-save", html)
        self.assertIn('id="settings-open"', html)
        self.assertIn('id="settings-dialog"', html)
        self.assertIn('id="confirm-dialog"', html)
        self.assertIn('id="sync-dialog"', html)
        self.assertEqual(html.count('id="settings-dialog"'), 1)
        self.assertEqual(html.count('id="settings-open"'), 1)
        self.assertEqual(html.count('id="sync-dialog"'), 1)
        self.assertIn('src="/static/textstrata-library-dev.js"', html)
        self.assertIn('JSON.stringify({presentation})', client_asset_content("library", "test", "test"))
        self.assertIn("filterEntries()", client_asset_content("library", "test", "test"))
        self.assertIn("Hide side chrome and keep the main reading area.", html)
        self.assertIn("Inspect, stop, or remove acquisition jobs.", html)
        self.assertIn("TextStrata", html)
        self.assertIn("Open import history", html)
        self.assertIn('id="new-note-link"', html)
        self.assertIn('href="/new"', html)
        self.assertNotIn('id="ingest-panel"', html)
        self.assertIn('data-revisions="note.frontend"', html)
        self.assertIn('data-trash-item="note.frontend"', html)

    def test_render_new_note_has_focused_ingest_workspace(self):
        html = render_new_note_html(PAPER_SKIN, version="0.2.0")
        self.assertIn('<title>New Note - TextStrata</title>', html)
        self.assertIn('role="tablist"', html)
        self.assertIn('data-source="url"', html)
        self.assertIn('data-source="file"', html)
        self.assertIn('data-source="text"', html)
        self.assertIn('id="ingest-url"', html)
        self.assertIn('id="ingest-file"', html)
        self.assertIn('id="keep-original"', html)
        self.assertIn('id="ocr-mode"', html)
        self.assertIn('id="acquire-notes"', html)
        self.assertIn('id="ingest-content"', html)
        self.assertIn('id="ingest-queue"', html)
        self.assertIn("/api/acquisition/ingest", client_asset_content("new-note", "0.2.0", "0.2.0"))
        self.assertIn("/api/acquisition/queue", client_asset_content("new-note", "0.2.0", "0.2.0"))
        self.assertIn("/api/asset/upload", client_asset_content("new-note", "0.2.0", "0.2.0"))
        self.assertIn("/api/ingest", client_asset_content("new-note", "0.2.0", "0.2.0"))
        self.assertIn('href="/?open=imports"', html)
        self.assertNotIn('id="ingest-panel"', html)

    def test_render_library_index_has_explicit_empty_state(self):
        html = render_library_index([], PAPER_SKIN)
        self.assertIn("Your knowledge base is empty", html)
        self.assertIn("0 notes", html)

    def test_whoami_route(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/whoami")
            resp = conn.getresponse()
            payload = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn('"service": "textstrata"', payload)
            self.assertIn('"port":', payload)
        finally:
            server.shutdown()
            server.server_close()
            app.close()


    def test_search_route_uses_worker_safe_catalog_connection(self):
        ingest_text(self.store, NOTE)
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/search?q=accessibility")
            resp = conn.getresponse()
            payload = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn("Frontend Notes", payload)
            self.assertIn("/item/note.frontend", payload)
        finally:
            server.shutdown()
            server.server_close()
            app.close()


    def test_json_ingest_publishes_indexes_and_returns_item(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps({"filename": "drop-note.md", "content": "# Dropped Note\n\nA searchable textstrata upload."})
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/ingest", body=body, headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 201)
            self.assertTrue(payload["published"])
            self.assertEqual(payload["item_id"], "drop-note")
            self.assertTrue((Path(self.tmp) / "normalized" / "drop-note.md").exists())

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/search?q=searchable")
            search = conn.getresponse()
            search_body = search.read().decode("utf-8")
            self.assertEqual(search.status, 200)
            self.assertIn("Dropped Note", search_body)
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_json_ingest_persists_ai_provenance_metadata(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps({
                "filename": "ai-provenance-test.md",
                "content": "# AI Provenance Test\n\nSynthetic note.",
                "contributor_chain": "via_ai",
                "ai_vendor": "OpenAI",
                "ai_model": "gpt-5.4 low",
                "ai_operation": "authored",
            })
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/ingest", body=body, headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 201)
            self.assertEqual(payload["item_id"], "ai-provenance-test")

            item = build_item((Path(self.tmp) / "normalized" / "ai-provenance-test.md").read_text(encoding="utf-8"), fallback_id="ai-provenance-test")[0]
            self.assertEqual(item.provenance.contributor_chain, "via_ai")
            self.assertEqual(item.provenance.ai_vendor, "OpenAI")
            self.assertEqual(item.provenance.ai_model, "gpt-5.4 low")
            self.assertEqual(item.provenance.ai_operation, "authored")
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_multipart_file_ingest_redirects_to_published_item(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            boundary = "textstrata-test-boundary"
            body = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"upload.md\"\r\n"
                "Content-Type: text/markdown; charset=utf-8\r\n\r\n# Uploaded Note\n\nMultipart body.\r\n"
                f"--{boundary}--\r\n"
            ).encode("utf-8")
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("POST", "/ingest", body=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 303)
            self.assertEqual(resp.getheader("Location"), "/item/upload")
            self.assertTrue((Path(self.tmp) / "normalized" / "upload.md").exists())
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_ingest_rejects_cross_origin_requests(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps({"content": "# Rejected"})
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("POST", "/api/ingest", body=body, headers={"Content-Type": "application/json", "Origin": "https://example.invalid"})
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 403)
            self.assertIsNone(self.store.normalized_path_for_id("rejected"))
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_youtube_ingest_rejects_when_yt_dlp_is_missing(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps({"url": "https://www.youtube.com/watch?v=f9ZBzEdB_N8", "notes": "preflight test"})
            with patch("textstrata.acquisition._tool", side_effect=lambda name: None if name == "yt-dlp" else "/usr/bin/true"):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/api/acquisition/ingest", body=body, headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
                resp = conn.getresponse()
                payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 400)
            self.assertEqual(payload["code"], "ingest-invalid")
            self.assertIn("yt-dlp", payload["error"])
            self.assertEqual(app.acquisition.list_jobs()["jobs"], [])
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_acquisition_date_helpers_extract_deterministic_source_dates(self):
        self.assertEqual(_normalize_source_date("20250102"), "2025-01-02T00:00:00+00:00")
        self.assertEqual(_normalize_source_date(1735776000), "2025-01-02T00:00:00+00:00")
        self.assertEqual(_format_transcript_stamp("00:02"), "00:02")
        self.assertEqual(_format_transcript_stamp("00:00:05"), "00:05")
        self.assertEqual(_format_transcript_stamp("01:02:03"), "01:02:03")
        html = b'<html><head><meta property="article:published_time" content="2024-05-06T07:08:09Z"></head></html>'
        self.assertEqual(_extract_html_source_date(html), "2024-05-06T07:08:09+00:00")

    def test_asset_upload_returns_reusable_asset_url(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            boundary = "textstrata-asset-boundary"
            png = b"\x89PNG\r\n\x1a\nPNGDATA"
            body = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="asset"; filename="clip.png"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode("utf-8") + png + f"\r\n--{boundary}--\r\n".encode("utf-8")
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("POST", "/api/asset/upload", body=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 201)
            self.assertTrue(payload["url"].startswith("/asset/"))
            self.assertEqual(payload["media_type"], "image/png")
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_item_save_route_rewrites_existing_note(self):
        ingest_text(self.store, NOTE)
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("POST", "/api/textstrata/item/note.frontend/save", body="# Frontend Notes\n\nUpdated body.", headers={"Content-Type": "text/plain; charset=utf-8", "Origin": f"http://127.0.0.1:{server.server_address[1]}"})
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 200)
            self.assertTrue(payload["saved"])
            saved = self.store.normalized_path_for_id("note.frontend").read_text(encoding="utf-8")
            self.assertIn("Updated body.", saved)
        finally:
            server.shutdown()
            server.server_close()
            app.close()

    def test_tag_route_preserves_saved_skin(self):
        ingest_text(self.store, NOTE)
        (Path(self.tmp) / "textstrata-settings.json").write_text(json.dumps({"revision_limit": 3, "presentation": {"skin": "console", "accent": "blue", "density": "compact", "font_scale": 105, "content_width": "wide", "card_style": "outlined", "motion": "reduced"}}), encoding="utf-8")
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/tag/accessibility")
            resp = conn.getresponse()
            payload = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn("TextStrata", payload)
            self.assertIn("--motion-duration:0ms", payload)
        finally:
            server.shutdown()
            server.server_close()
            app.close()



class ApplicationUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)

    def test_application_settings_payloads_delegate_exactly(self):
        saved = save_settings_payload(self.store, {"revision_limit": 3, "presentation": {"skin": "console", "accent": "blue", "density": "compact", "font_scale": 105, "content_width": "wide", "card_style": "outlined", "motion": "reduced"}})
        self.assertEqual(saved, load_settings_payload(self.store))
        self.assertEqual(saved["revision_limit"], 3)
        app = SimpleNamespace(system_info=lambda: {"service": "textstrata", "version": "0.2.0"})
        self.assertEqual(build_system_info_payload(app), {"service": "textstrata", "version": "0.2.0"})

    def test_application_acquisition_payloads_delegate_exactly(self):
        class StubAcquisition:
            def __init__(self):
                self.saved = None
                self.enqueued = []
            def list_jobs(self):
                return {"jobs": [{"id": 1}], "running": 0}
            def get_settings(self):
                return {"retain_original": True, "retention_days": 30}
            def save_settings(self, payload):
                self.saved = payload
                return {"saved": payload}
            def clear_completed(self):
                return 3
            def enqueue_url(self, url, **kwargs):
                self.enqueued.append(("url", url, kwargs))
                return 11
            def enqueue_file(self, blob, filename, media_type="", **kwargs):
                self.enqueued.append(("file", filename, media_type, kwargs, blob))
                return 12

        svc = StubAcquisition()
        self.assertEqual(acquisition_queue_payload(svc), {"jobs": [{"id": 1}], "running": 0})
        self.assertEqual(acquisition_maintenance_settings_payload(svc), {"retain_original": True, "retention_days": 30})
        self.assertEqual(save_acquisition_maintenance_settings(svc, {"retention_days": 45}), {"saved": {"retention_days": 45}})
        self.assertEqual(clear_acquisition_completed(svc), {"cleared": 3})
        self.assertEqual(build_ingest_submission(svc, {"url": " https://example.com/x ", "keep_original": "true", "ocr_mode": "text", "title": "T", "notes": "N"}), {"job_id": 11, "status": "queued"})
        self.assertEqual(build_ingest_submission(svc, {"file": b"data", "filename": "a.txt", "media_type": "text/plain", "ocr_mode": "invalid"}), {"job_id": 12, "status": "queued"})
        self.assertEqual(svc.enqueued[0][0], "url")
        self.assertEqual(svc.enqueued[1][0], "file")


if __name__ == "__main__":
    unittest.main()
