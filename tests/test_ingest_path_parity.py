"""Task 16 - normalized bytes must not depend on how the input arrived.

Guarantee 1 says identical input produces identical normalized output. Task 0d
established that for repeated ingests of the same input through the same door.
This is the other half: the same document must normalize identically whether it
is ingested from disk or saved through the web editor.

It does not today. ``ingest_file`` translates newlines explicitly as part of
Task 7 (``raw_bytes.decode("utf-8").replace("\\r\\n", "\\n").replace("\\r", "\\n")``)
while the web route's ``_read_raw_text`` only decodes. So a CRLF document keeps
its CRLF through the editor and loses it from disk.

The divergence is not limited to line endings in the body. A carriage return
inside front matter lands on the id VALUE, ``is_valid_id`` rejects it, and the
item falls back to a slug derived from the fallback id. So the same CRLF
document becomes ``note.dual`` from disk and ``web-ingest`` through the editor.
Identity, not just bytes.

RULED DIRECTION: the web path adopts ingest_file's newline translation. That
path is already specified and verified by Task 7, and originals are preserved
either way, so nothing about byte preservation changes.

Mixed line endings within one document are deliberately not tested. There is no
obviously correct normalization for them and no caller is known to produce them.
"""

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from textstrata.ingest import build_item, ingest_file
from textstrata.store import TextStrataStore
from textstrata.web import TextStrataWebApp, create_handler


ITEM_ID = "note.parity"
BOM = "﻿"

# Front matter and body both use LF here; variants below rewrite the whole
# document, which is the point.
DOC_LF = (
    f"---\nid: {ITEM_ID}\ntitle: Parity\ntype: note\ntags: [x]\n---\n"
    "\nline one\nline two\n"
)
DOC_CRLF = DOC_LF.replace("\n", "\r\n")

VARIANTS = {
    "lf": DOC_LF,
    "crlf": DOC_CRLF,
    "bom+lf": BOM + DOC_LF,
    "bom+crlf": BOM + DOC_CRLF,
}


class _ParityCase(unittest.TestCase):
    def via_disk(self, text):
        directory = Path(tempfile.mkdtemp())
        path = directory / f"{ITEM_ID}.md"
        path.write_bytes(text.encode("utf-8"))
        store = TextStrataStore(tempfile.mkdtemp())
        result = ingest_file(store, path)
        self.assertTrue(result.published, result.validation.errors)
        return result.item.id, result.normalized_path.read_bytes()

    def via_web(self, text):
        tmp = tempfile.mkdtemp()
        store = TextStrataStore(tmp)
        app = TextStrataWebApp(workspace_root=Path(tmp))
        self.addCleanup(app.close)
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/ingest",
            data=json.dumps({"content": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload.get("published"), payload)
        ids = [path.stem for path in store.normalized_paths()]
        self.assertEqual(len(ids), 1, f"expected one item, got {ids}")
        return ids[0], store.normalized_path_for_id(ids[0]).read_bytes()


class FixtureGuardTests(unittest.TestCase):
    """Standing guard: a CRLF fixture must still target the intended id.

    A carriage return inside front matter leaves ``\\r`` on the id value, which
    slugifies to a different id. A fixture that trips that silently measures a
    sibling item instead of the one under test. This has caught real mistakes in
    this suite three times, so every CRLF fixture is checked here first.
    """

    def test_the_lf_fixture_targets_the_intended_id(self):
        item, _suggested, _fm = build_item(DOC_LF, fallback_id="fallback")
        self.assertEqual(item.id, ITEM_ID)

    def test_the_crlf_fixtures_carry_real_carriage_returns(self):
        for name in ("crlf", "bom+crlf"):
            with self.subTest(variant=name):
                self.assertIn("\r\n", VARIANTS[name])

    def test_the_crlf_fixture_currently_loses_its_id(self):
        """Documents the mechanism this task removes.

        Asserted so the fixture guard above cannot be mistaken for paranoia:
        the id really does change today, and Task 16 is what fixes it.
        """
        item, _suggested, _fm = build_item(DOC_CRLF, fallback_id="fallback")
        self.assertNotEqual(item.id, ITEM_ID)


class NormalizedBytesAreIndependentOfTheDoorTests(_ParityCase):
    def test_each_variant_normalizes_identically_from_either_path(self):
        for name, text in VARIANTS.items():
            with self.subTest(variant=name):
                disk_id, disk_bytes = self.via_disk(text)
                web_id, web_bytes = self.via_web(text)
                self.assertEqual(
                    disk_id, web_id,
                    f"{name}: the two paths produced different item ids",
                )
                self.assertEqual(
                    disk_bytes, web_bytes,
                    f"{name}: the two paths produced different normalized bytes",
                )

    def test_every_variant_reaches_the_declared_id(self):
        for name, text in VARIANTS.items():
            with self.subTest(variant=name):
                self.assertEqual(self.via_disk(text)[0], ITEM_ID)
                self.assertEqual(self.via_web(text)[0], ITEM_ID)

    def test_all_four_variants_agree_with_each_other(self):
        """The strongest form: line endings and a BOM are presentation, not content."""
        outputs = {name: self.via_web(text)[1] for name, text in VARIANTS.items()}
        baseline = outputs["lf"]
        for name, blob in outputs.items():
            with self.subTest(variant=name):
                self.assertEqual(blob, baseline, f"{name} diverged from the lf baseline")

    def test_normalized_output_never_carries_crlf(self):
        for name, text in VARIANTS.items():
            with self.subTest(variant=name):
                self.assertNotIn(b"\r\n", self.via_web(text)[1])
                self.assertNotIn(b"\r\n", self.via_disk(text)[1])

    def test_normalized_output_never_carries_a_bom(self):
        for name, text in VARIANTS.items():
            with self.subTest(variant=name):
                self.assertFalse(self.via_web(text)[1].startswith(b"\xef\xbb\xbf"))
                self.assertFalse(self.via_disk(text)[1].startswith(b"\xef\xbb\xbf"))


class OriginalsAreStillPreservedTests(_ParityCase):
    """Task 16 must not cost Task 7. Newline translation applies to what gets
    PARSED, never to the bytes stored as the original."""

    def test_the_disk_path_still_stores_exact_bytes(self):
        for name, text in VARIANTS.items():
            with self.subTest(variant=name):
                directory = Path(tempfile.mkdtemp())
                path = directory / f"{ITEM_ID}.md"
                path.write_bytes(text.encode("utf-8"))
                store = TextStrataStore(tempfile.mkdtemp())
                result = ingest_file(store, path)
                self.assertEqual(result.original_path.read_bytes(), text.encode("utf-8"))

    def test_the_web_path_still_stores_exact_bytes_on_a_first_write(self):
        for name, text in VARIANTS.items():
            with self.subTest(variant=name):
                tmp = tempfile.mkdtemp()
                store = TextStrataStore(tmp)
                app = TextStrataWebApp(workspace_root=Path(tmp))
                self.addCleanup(app.close)
                server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
                server.daemon_threads = True
                threading.Thread(target=server.serve_forever, daemon=True).start()
                self.addCleanup(server.server_close)
                self.addCleanup(server.shutdown)
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/ingest",
                    data=json.dumps({"content": text}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    json.loads(response.read().decode("utf-8"))
                originals = sorted(store.original_dir.iterdir())
                self.assertEqual(len(originals), 1)
                self.assertEqual(originals[0].read_bytes(), text.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
