"""Task 7 — the original must be preserved as bytes, not as decoded text.

``ingest_file`` calls ``path.read_text(encoding="utf-8")``, so the file's real
bytes are gone before ``save_original`` is ever reached: universal newlines have
already collapsed CRLF to LF. ``save_original`` then re-encodes through
``_atomic_write``, which opens in text mode. There is no point in the program
where the original bytes exist, so no amount of work inside ``store.py`` can fix
this. The bytes path has to start in ``ingest_file``.

§4 adds ``save_original_bytes``, ``verify_original``, and an ``original_sha256``
recorded at save time so tampering is detectable after the fact.
"""

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from textstrata.ingest import build_item, ingest_file, ingest_text
from textstrata.store import TextStrataStore
from textstrata.web import TextStrataWebApp, create_handler


BOM = b"\xef\xbb\xbf"
CRLF_BOM = (
    BOM
    + b"---\r\n"
    + b"id: note.crlf\r\n"
    + b"title: CRLF Note\r\n"
    + b"type: note\r\n"
    + b"tags: [x]\r\n"
    + b"---\r\n"
    + b"\r\n"
    + b"body line one\r\n"
    + b"body line two\r\n"
)

PLAIN = "---\nid: note.plain\ntitle: Plain\ntype: note\ntags: [x]\n---\n\nbody\n"


def fresh_store():
    return TextStrataStore(tempfile.mkdtemp())


def write(raw: bytes, name="note.crlf.md") -> Path:
    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(raw)
    return path


class OriginalBytesRoundTripTests(unittest.TestCase):
    def test_crlf_and_bom_survive_ingest_file_byte_for_byte(self):
        result = ingest_file(fresh_store(), write(CRLF_BOM))
        self.assertTrue(result.published, result.validation.errors)
        self.assertEqual(
            result.original_path.read_bytes(),
            CRLF_BOM,
            "the original was decoded and re-encoded instead of stored as bytes",
        )

    def test_crlf_line_endings_are_not_collapsed(self):
        result = ingest_file(fresh_store(), write(CRLF_BOM))
        stored = result.original_path.read_bytes()
        self.assertEqual(stored.count(b"\r\n"), CRLF_BOM.count(b"\r\n"))

    def test_a_lone_bom_is_preserved(self):
        result = ingest_file(fresh_store(), write(BOM + PLAIN.encode("utf-8")))
        self.assertTrue(result.original_path.read_bytes().startswith(BOM))

    def test_a_rejected_file_still_preserves_its_bytes(self):
        bad = BOM + b"---\r\nid: Bad Id\r\ntitle: Nope\r\n---\r\n\r\nbody\r\n"
        result = ingest_file(fresh_store(), write(bad, "bad.md"))
        self.assertFalse(result.published)
        self.assertEqual(result.original_path.read_bytes(), bad)


class SaveOriginalBytesApiTests(unittest.TestCase):
    def test_save_original_bytes_writes_exactly_what_it_is_given(self):
        store = fresh_store()
        store.ensure_dirs()
        path = store.save_original_bytes("note.raw", CRLF_BOM)
        self.assertEqual(path.read_bytes(), CRLF_BOM)

    def test_it_round_trips_bytes_that_are_not_valid_utf8(self):
        """§4 calls this a bytes API, so it must not assume decodable input.

        Added during Task 7 verification. Without it the suite cannot tell a
        binary write from a decode-then-text-mode write: on POSIX those produce
        identical bytes for any UTF-8 input, so every CRLF/BOM assertion passes
        either way. A byte that is not valid UTF-8 separates them, and would
        also separate them on a platform where text mode rewrites newlines.
        """
        store = fresh_store()
        store.ensure_dirs()
        # Latin-1 accented byte: a real file, not decodable as UTF-8.
        raw = b"---\nid: note.bin\ntitle: Caf\xe9\n---\n\nbody\n"
        path = store.save_original_bytes("note.bin", raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(store.verify_original("note.bin")["matches"])

    def test_save_original_remains_a_text_wrapper(self):
        store = fresh_store()
        store.ensure_dirs()
        path = store.save_original("note.plain", PLAIN)
        self.assertEqual(path.read_text(encoding="utf-8"), PLAIN)


class VerifyOriginalTests(unittest.TestCase):
    def test_an_untouched_original_verifies(self):
        store = fresh_store()
        result = ingest_text(store, PLAIN)
        report = store.verify_original("note.plain")
        self.assertTrue(report["matches"])
        self.assertEqual(report["item_id"], "note.plain")
        self.assertEqual(report["expected_sha256"], report["actual_sha256"])
        self.assertEqual(
            report["expected_sha256"],
            hashlib.sha256(result.original_path.read_bytes()).hexdigest(),
        )

    def test_a_tampered_original_does_not_verify(self):
        store = fresh_store()
        result = ingest_text(store, PLAIN)
        result.original_path.write_bytes(b"tampered\n")
        report = store.verify_original("note.plain")
        self.assertFalse(report["matches"])
        self.assertNotEqual(report["expected_sha256"], report["actual_sha256"])

    def test_a_missing_original_does_not_verify(self):
        store = fresh_store()
        result = ingest_text(store, PLAIN)
        result.original_path.unlink()
        report = store.verify_original("note.plain")
        self.assertFalse(report["matches"])
        self.assertIsNone(report["actual_sha256"])

    def test_an_unknown_item_raises(self):
        store = fresh_store()
        store.ensure_dirs()
        with self.assertRaises(FileNotFoundError):
            store.verify_original("note.never-seen")

    def test_the_recorded_hash_is_of_the_raw_bytes_not_the_decoded_text(self):
        store = fresh_store()
        ingest_file(store, write(CRLF_BOM))
        report = store.verify_original("note.crlf")
        self.assertTrue(report["matches"])
        self.assertEqual(report["expected_sha256"], hashlib.sha256(CRLF_BOM).hexdigest())



class _WebCase(unittest.TestCase):
    """Shared harness: a live server over a temp workspace."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)
        self.app = TextStrataWebApp(workspace_root=Path(self.tmp))
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.original = self.store.original_dir / "note.web.md"

    def _post(self, path, content):
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps({"content": content}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def create(self, content):
        """First write, through the public ingest route."""
        return self._post("/api/ingest", content)

    def edit(self, content):
        """Update, through the item-save route."""
        return self._post("/api/textstrata/item/note.web/save", content)

    def normalized(self):
        return self.store.normalized_path_for_id("note.web").read_bytes()

    def item_ids(self):
        return sorted(path.stem for path in self.store.normalized_paths())


PLAIN_FIRST = "---\nid: note.web\ntitle: First\ntype: note\ntags: [x]\n---\n\nfirst body\n"
PLAIN_SECOND = "---\nid: note.web\ntitle: Second\ntype: note\ntags: [x]\n---\n\nsecond body\n"
BOM_FIRST = "\ufeff" + PLAIN_FIRST
BOM_SECOND = "\ufeff" + PLAIN_SECOND


class WebFirstWriteStoresSubmittedBytesTests(_WebCase):
    """Task 7's bar, now scoped to the FIRST write.

    Under the write-once ruling the original is whatever bytes first created the
    item, permanently. That makes this the only write where Task 7's byte
    equality can be asserted at all, so it is asserted here rather than on an
    edit. A BOM-prefixed creation is included because ``web.py`` classifies
    whole-document versus body-only submissions by looking for a leading
    ``---``, and a BOM is not whitespace.
    """

    def test_a_plain_first_write_stores_the_submitted_bytes(self):
        status, _payload = self.create(PLAIN_FIRST)
        self.assertIn(status, (200, 201))
        self.assertEqual(self.original.read_bytes(), PLAIN_FIRST.encode("utf-8"))

    def test_a_bom_prefixed_first_write_stores_the_submitted_bytes(self):
        status, _payload = self.create(BOM_FIRST)
        self.assertIn(status, (200, 201))
        self.assertEqual(self.original.read_bytes(), BOM_FIRST.encode("utf-8"))
        self.assertTrue(self.original.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_the_first_write_stores_exactly_one_front_matter_block(self):
        self.create(BOM_FIRST)
        stored = self.original.read_bytes()
        self.assertNotIn(b"retrieval_priority", stored)
        self.assertEqual(stored.count(b"id: note.web"), 1)

    def test_verify_original_is_not_satisfied_by_a_rebuilt_document(self):
        """Kept as written, rescoped to the first write.

        Write-once does not bear on this property: it says which bytes become
        the original, not whether the recorded hash may describe a document
        nobody submitted. The recorded hash must be the hash of the submission
        either way, so the assertion survives unchanged in substance.
        """
        self.create(BOM_FIRST)
        report = self.store.verify_original("note.web")
        self.assertEqual(
            report["expected_sha256"],
            hashlib.sha256(BOM_FIRST.encode("utf-8")).hexdigest(),
            "the recorded hash describes a document the user never submitted",
        )
        self.assertTrue(report["matches"])


class WebUpdateDoesNotTouchTheOriginalTests(_WebCase):
    """The update-path counterpart, per the write-once ruling.

    An edit is an update, not a new original. It must succeed, its content must
    reach the normalized file, and the stored original and its recorded hash
    must both be untouched. Verified against a simulated Task 8a: naive
    write-once alone returns 409 and drops the edit entirely, which is why the
    recoverability assertions below are part of this task rather than a later
    cleanup.
    """

    def setUp(self):
        super().setUp()
        status, _payload = self.create(PLAIN_FIRST)
        self.assertIn(status, (200, 201))
        self.first_bytes = self.original.read_bytes()
        self.first_sha = self.store.verify_original("note.web")["expected_sha256"]

    def test_the_edit_succeeds(self):
        status, payload = self.edit(PLAIN_SECOND)
        self.assertEqual(status, 200, payload)

    def test_the_edit_reaches_the_normalized_file(self):
        self.edit(PLAIN_SECOND)
        self.assertIn(b"second body", self.normalized())

    def test_the_stored_original_is_unchanged(self):
        self.edit(PLAIN_SECOND)
        self.assertEqual(
            self.original.read_bytes(), self.first_bytes,
            "the edit replaced the original; preserve_exact means the first bytes are permanent",
        )

    def test_the_recorded_hash_is_unchanged(self):
        self.edit(PLAIN_SECOND)
        self.assertEqual(
            self.store.verify_original("note.web")["expected_sha256"], self.first_sha
        )
        self.assertTrue(self.store.verify_original("note.web")["matches"])

    def test_the_edit_is_not_silently_dropped(self):
        """Data-loss guard. Naive write-once 409s and loses the edit."""
        status, payload = self.edit(PLAIN_SECOND)
        self.assertEqual(status, 200, payload)
        self.assertIn(b"second body", self.normalized())
        self.assertNotIn(b"first body", self.normalized())

    def test_the_previous_state_is_recoverable_as_a_revision(self):
        self.edit(PLAIN_SECOND)
        revisions = self.store.list_revisions("note.web")
        self.assertTrue(revisions, "no revision captured the pre-edit state")
        holds = [
            (self.store.revision_dir / "note.web" / str(entry["name"])).read_bytes()
            for entry in revisions
        ]
        self.assertTrue(
            any(b"first body" in blob for blob in holds),
            "the pre-edit content is not recoverable from revisions",
        )

    def test_repeated_edits_keep_accumulating_history(self):
        self.edit(PLAIN_SECOND)
        third = PLAIN_SECOND.replace("second body", "third body").replace("Second", "Third")
        status, payload = self.edit(third)
        self.assertEqual(status, 200, payload)
        self.assertIn(b"third body", self.normalized())
        self.assertEqual(self.original.read_bytes(), self.first_bytes)
        self.assertGreaterEqual(len(self.store.list_revisions("note.web")), 2)

    def test_a_bom_prefixed_edit_does_not_create_a_sibling(self):
        """Identity guard, re-confirmed under write-once.

        The original is no longer rewritten on an edit, so a mangled id would
        not show up as a second original file. It would show up as a second
        NORMALIZED item, which is what this asserts.
        """
        before = self.item_ids()
        self.assertEqual(before, ["note.web"])
        status, payload = self.edit(BOM_SECOND)
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.item_ids(), before, "the edit created a sibling item")
        self.assertEqual(payload["item_id"], "note.web")
        self.assertEqual(
            sorted(path.name for path in self.store.original_dir.iterdir()),
            ["note.web.md"],
        )

    def test_a_bom_prefixed_edit_still_leaves_the_original_alone(self):
        self.edit(BOM_SECOND)
        self.assertEqual(self.original.read_bytes(), self.first_bytes)
        self.assertIn(b"second body", self.normalized())

    def test_no_edit_shape_reaches_an_original_writing_call(self):
        """Behavioural assertions can be satisfied by a branch that writes the
        same bytes back. This watches the calls themselves, across the edit
        shapes most likely to take a different branch, including the body-only
        submission that gets its front matter spliced in and an identical
        resave that a write-once no-op would let through silently.
        """
        shapes = {
            "plain": PLAIN_SECOND,
            "body only": "just replacement body text",
            "bom": BOM_SECOND,
            "identical resave": PLAIN_FIRST,
        }
        for label, content in shapes.items():
            with self.subTest(shape=label):
                calls = []
                real_text = TextStrataStore.save_original
                real_bytes = TextStrataStore._save_original_bytes

                def spy_text(store, item_id, raw_text, _real=real_text):
                    calls.append("save_original")
                    return _real(store, item_id, raw_text)

                def spy_bytes(store, item_id, raw, sha, _real=real_bytes):
                    calls.append("_save_original_bytes")
                    return _real(store, item_id, raw, sha)

                with patch.object(TextStrataStore, "save_original", spy_text), \
                        patch.object(TextStrataStore, "_save_original_bytes", spy_bytes):
                    status, payload = self.edit(content)
                self.assertEqual(status, 200, payload)
                self.assertEqual(
                    calls, [], f"{label}: the update path wrote an original"
                )


class AnEditsExactBytesAreNotPreservedTests(_WebCase):
    """ACCEPTED CONSEQUENCE of preserve_exact, pinned deliberately.

    This is intended behaviour, not an oversight, and it is asserted so that it
    stays a decision rather than becoming an unnoticed regression later.

    ``preserve_exact`` means the bytes that FIRST created an item are its
    original, permanently. An edit is an update, so its submitted bytes are
    never stored as an original. What the edit produces instead is normalized
    output, which is a canonical rendering: LF line endings, no BOM, canonical
    key order. So an edit's CONTENT is fully recoverable, from the normalized
    file and from the revision history, while its exact submitted BYTES are not
    recoverable from anywhere in the workspace.

    The trade is deliberate. Preserving every edit's bytes would mean either
    rewriting the original, which write-once forbids, or introducing a second
    permanent byte store that nothing else in the contract asks for.

    A CRLF assertion previously lived here, recording that the web path carries
    body CRLF through while ingest_file translates it. That divergence has since
    been ruled a determinism gap rather than an accepted consequence, so it moved
    to tests/test_ingest_path_parity.py where it is expected to be red until Task
    16 lands. Nothing else in this class changes: a BOM is still normalized away,
    the original still keeps the first write's bytes, and an edit's exact bytes
    are still nowhere in the workspace.
    """

    # A second edit carrying both markers the normalizer removes: a BOM at the
    # very start, and CRLF line endings in the BODY.
    #
    # The front matter deliberately uses LF. CRLF inside front matter puts a
    # trailing \r on the id value, which slugifies to a different id, so the
    # save would create a sibling item and every assertion below would be
    # reading the wrong file. That failure mode is covered separately by the
    # identity guard; it must not be what this class accidentally measures.
    CRLF_BOM_SECOND = (
        "\ufeff---\nid: note.web\ntitle: Second\ntype: note\ntags: [x]\n---\n"
        "\r\nsecond body\r\nmore text\r\n"
    )

    def test_the_fixture_targets_the_right_item(self):
        """Guard against the fixture silently creating a sibling."""
        item, _suggested, _fm = build_item(self.CRLF_BOM_SECOND, fallback_id="note.web")
        self.assertEqual(item.id, "note.web")
        self.assertIn("\r\n", item.body, "fixture no longer carries CRLF in the body")

    def setUp(self):
        super().setUp()
        status, _payload = self.create(PLAIN_FIRST)
        self.assertIn(status, (200, 201))
        self.first_bytes = self.original.read_bytes()

    def test_the_edit_succeeds_and_its_content_survives(self):
        status, payload = self.edit(self.CRLF_BOM_SECOND)
        self.assertEqual(status, 200, payload)
        self.assertIn(b"second body", self.normalized())

    def test_the_bom_is_normalized_away(self):
        self.edit(self.CRLF_BOM_SECOND)
        self.assertFalse(
            self.normalized().startswith(b"\xef\xbb\xbf"),
            "normalized output should not carry a BOM",
        )

    def test_the_original_keeps_the_first_writes_bytes(self):
        self.edit(self.CRLF_BOM_SECOND)
        self.assertEqual(self.original.read_bytes(), self.first_bytes)

    def test_the_edits_exact_bytes_are_nowhere_in_the_workspace(self):
        """The accepted loss, stated as an assertion.

        If a future change starts preserving an edit's bytes, this fails and
        the decision gets revisited on purpose rather than by accident.
        """
        self.edit(self.CRLF_BOM_SECOND)
        needle = self.CRLF_BOM_SECOND.encode("utf-8")
        for path in sorted(Path(self.tmp).rglob("*")):
            if not path.is_file():
                continue
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            self.assertNotIn(
                needle, blob, f"an edit's exact bytes were preserved at {path}"
            )

    def test_the_content_remains_recoverable_despite_the_byte_loss(self):
        self.edit(self.CRLF_BOM_SECOND)
        self.assertIn(b"second body", self.normalized())
        revisions = self.store.list_revisions("note.web")
        self.assertTrue(revisions)
        holds = [
            (self.store.revision_dir / "note.web" / str(entry["name"])).read_bytes()
            for entry in revisions
        ]
        self.assertTrue(any(b"first body" in blob for blob in holds))


if __name__ == "__main__":
    unittest.main()
