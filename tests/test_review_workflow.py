import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from textstrata import review
from textstrata.application.reviews import review_queue_payload
from textstrata.ingest import ingest_text
from textstrata.presentation import PAPER_SKIN, render_library_index
from textstrata.store import TextStrataStore
from textstrata.web import TextStrataWebApp, create_handler


NOTE = """---
id: note.review-context
title: Review Context
type: reference
tags: [existing]
---

# Review Context

This body gives a reviewer enough context to evaluate a proposed taxonomy tag.
"""


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)
        self.result = ingest_text(self.store, NOTE)
        review.enqueue(
            self.store,
            self.result.item.id,
            "Stale queued title",
            ["taxonomy"],
        )

    def test_review_payload_uses_current_item_context_without_mutating_queue(self):
        payload = review_queue_payload(self.store, [self.result.item])

        self.assertEqual(payload["count"], 1)
        entry = payload["pending"][0]
        self.assertEqual(entry["item_title"], "Review Context")
        self.assertEqual(entry["current_tags"], ["existing"])
        self.assertIn("enough context", entry["body_excerpt"])
        self.assertTrue(entry["item_exists"])
        self.assertNotIn("current_tags", review.list_pending(self.store)[0])

    def test_review_payload_marks_stale_queue_entries(self):
        payload = review_queue_payload(self.store, [])

        entry = payload["pending"][0]
        self.assertFalse(entry["item_exists"])
        self.assertEqual(entry["current_tags"], [])
        self.assertEqual(entry["body_excerpt"], "")

    def test_library_has_first_class_review_dialog(self):
        html = render_library_index([self.result.item], PAPER_SKIN)

        self.assertEqual(html.count('id="review-dialog"'), 1)
        self.assertIn('data-action="review-queue"', html)
        self.assertIn("Current tags", html)
        self.assertIn("Apply suggested tags", html)
        self.assertIn("Reject suggestion", html)
        self.assertIn('openTarget === "review"', html)

    def test_review_http_round_trip_returns_context_and_applies_tags(self):
        app = TextStrataWebApp(self.tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            conn.request("GET", "/api/textstrata/review")
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["pending"][0]["current_tags"], ["existing"])
            self.assertIn("enough context", payload["pending"][0]["body_excerpt"])

            body = json.dumps({"item_id": self.result.item.id, "tags": ["taxonomy"]})
            conn.request(
                "POST",
                "/api/textstrata/review/confirm",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
            self.assertIn("taxonomy", app.item_by_id(self.result.item.id).tags)
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            app.close()


if __name__ == "__main__":
    unittest.main()
