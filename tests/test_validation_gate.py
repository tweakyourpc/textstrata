"""Task 2 — merge conflicts must reach the validation gate.

``frontmatter.parse`` records conflicts, ``build_item`` hands them back on the
``MergedFrontmatter``, and ``ingest_text`` copies them onto
``IngestResult.frontmatter_conflicts``. Nothing else ever looks at them.
``validate()`` is not given them and cannot ask for them, so an appended block
that contradicts the first on ``id``, ``type``, ``title``, ``handling``,
``preservation``, ``dependencies`` or ``related`` publishes anyway. The
contradiction is reported to a caller that has already written the file.

Conflicts on the seven protected keys are errors. Every other conflict is a
warning. Diagnostics travel through ``validate(item, diagnostics=...)`` and
``IngestResult`` only -- never through ``TextStrataItem.extra``, which
round-trips to disk and would make a clean file inherit stale conflicts.
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from textstrata.ingest import build_item, ingest_text
from textstrata.store import TextStrataStore
from textstrata.validate import validate
from textstrata.web import TextStrataWebApp, create_handler


PROTECTED_CONTRADICTIONS = (
    ("id", "note.first", "note.second"),
    ("type", "note", "playbook"),
    ("title", "First", "Second"),
    ("handling", "human_plus_ai", "ai_only"),
    ("preservation", "preserve_exact", "rewrite_allowed"),
    ("dependencies", "note.c", "note.d"),
    ("related", "note.a", "note.b"),
)

UNPROTECTED_CONTRADICTIONS = (
    ("created_via", "tool-a", "tool-b"),
    ("authorship", "Ada", "Grace"),
)


def contradicting(field, first, second):
    """Two stacked blocks that disagree on ``field``. Block 1 carries identity."""
    identity = "" if field in ("id", "title", "type") else "id: note.gate\ntitle: Gate\ntype: note\n"
    head = {
        "id": "title: Gate\ntype: note\n",
        "title": "id: note.gate\ntype: note\n",
        "type": "id: note.gate\ntitle: Gate\n",
    }.get(field, "")
    return (
        f"---\n{identity}{head}tags: [x]\n{field}: {first}\n---\n"
        f"---\n{field}: {second}\n---\n\nbody\n"
    )


def fresh_store():
    return TextStrataStore(tempfile.mkdtemp())


class ProtectedConflictsBlockPublicationTests(unittest.TestCase):
    def test_a_contradicting_block_does_not_publish(self):
        for field, first, second in PROTECTED_CONTRADICTIONS:
            with self.subTest(key=field):
                result = ingest_text(fresh_store(), contradicting(field, first, second))
                self.assertTrue(
                    result.frontmatter_conflicts,
                    f"{field}: no conflict was recorded, so this case proves nothing",
                )
                self.assertFalse(
                    result.published,
                    f"{field}: contradicting block published anyway",
                )

    def test_the_conflict_is_reported_as_a_validation_error(self):
        for field, first, second in PROTECTED_CONTRADICTIONS:
            with self.subTest(key=field):
                result = ingest_text(fresh_store(), contradicting(field, first, second))
                conflict = result.frontmatter_conflicts[0]
                self.assertTrue(
                    any(conflict in error for error in result.validation.errors),
                    f"{field}: conflict {conflict!r} absent from errors {result.validation.errors}",
                )

    def test_no_original_is_lost_when_the_gate_rejects(self):
        result = ingest_text(fresh_store(), contradicting("id", "note.first", "note.second"))
        self.assertFalse(result.published)
        self.assertIsNone(result.normalized_path)
        self.assertIsNotNone(result.original_path)


class UnprotectedConflictsWarnTests(unittest.TestCase):
    def test_a_contradicting_unprotected_key_still_publishes(self):
        for field, first, second in UNPROTECTED_CONTRADICTIONS:
            with self.subTest(key=field):
                result = ingest_text(fresh_store(), contradicting(field, first, second))
                self.assertTrue(result.frontmatter_conflicts)
                self.assertTrue(result.published, result.validation.errors)

    def test_a_contradicting_unprotected_key_is_reported_as_a_warning(self):
        for field, first, second in UNPROTECTED_CONTRADICTIONS:
            with self.subTest(key=field):
                result = ingest_text(fresh_store(), contradicting(field, first, second))
                conflict = result.frontmatter_conflicts[0]
                self.assertTrue(
                    any(conflict in warning for warning in result.validation.warnings),
                    f"{field}: conflict {conflict!r} absent from warnings {result.validation.warnings}",
                )
                self.assertEqual(result.validation.errors, [])


class ValidateSignatureTests(unittest.TestCase):
    def test_validate_without_diagnostics_is_unchanged(self):
        item, _, _ = build_item(contradicting("id", "note.first", "note.second"))
        result = validate(item)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.errors, [])

    def test_validate_accepts_diagnostics_and_gates_on_them(self):
        item, _, fm = build_item(contradicting("id", "note.first", "note.second"))
        result = validate(item, diagnostics=fm.conflicts)
        self.assertFalse(result.ok)
        self.assertTrue(any(fm.conflicts[0] in error for error in result.errors))

    def test_validate_with_empty_diagnostics_matches_the_bare_call(self):
        item, _, _ = build_item(contradicting("id", "note.first", "note.second"))
        self.assertEqual(validate(item, diagnostics=[]).errors, validate(item).errors)


class DiagnosticsAreNeverSerializedTests(unittest.TestCase):
    """§4: diagnostics must not reach ``extra``, which round-trips to disk."""

    def test_conflicts_do_not_land_in_item_extra(self):
        for field, first, second in PROTECTED_CONTRADICTIONS + UNPROTECTED_CONTRADICTIONS:
            with self.subTest(key=field):
                item, _, fm = build_item(contradicting(field, first, second))
                self.assertTrue(fm.conflicts)
                for value in item.extra.values():
                    self.assertNotIn(fm.conflicts[0], str(value))
                for key in item.extra:
                    self.assertNotIn("conflict", key.lower())
                    self.assertNotIn("diagnostic", key.lower())

    def test_conflict_text_is_not_written_into_the_normalized_file(self):
        store = fresh_store()
        result = ingest_text(store, contradicting("created_via", "tool-a", "tool-b"))
        self.assertTrue(result.published, result.validation.errors)
        published = result.normalized_path.read_text(encoding="utf-8")
        self.assertNotIn("tool-b", published)
        self.assertNotIn("rejected", published)

    def test_a_normalized_file_reingested_produces_zero_conflicts(self):
        # The round-trip trap: stashing diagnostics in ``extra`` fails here.
        store = fresh_store()
        first = ingest_text(store, contradicting("created_via", "tool-a", "tool-b"))
        self.assertTrue(first.published, first.validation.errors)
        self.assertTrue(first.frontmatter_conflicts)

        normalized = first.normalized_path.read_text(encoding="utf-8")
        second = ingest_text(fresh_store(), normalized)
        self.assertEqual(
            second.frontmatter_conflicts,
            [],
            "a clean normalized file inherited stale conflicts on re-ingest",
        )
        self.assertTrue(second.published, second.validation.errors)
        self.assertEqual(second.validation.warnings, [])

        # Counting conflicts is not enough on its own. A re-ingested file has a
        # single block, so no new conflict can arise however the first ingest
        # stored its diagnostics -- this assertion passes even when the conflict
        # text was written to disk under ``extra``. §4's actual requirement is
        # that the clean file does not *inherit* the diagnostic, so check the
        # item and the bytes directly.
        for key, value in second.item.extra.items():
            self.assertNotIn("conflict", key.lower())
            self.assertNotIn("diagnostic", key.lower())
            self.assertNotIn("kept", str(value))
            self.assertNotIn("rejected", str(value))
        self.assertNotIn("kept", normalized)
        self.assertNotIn("rejected", normalized)

    def test_the_round_trip_is_stable_for_a_clean_file(self):
        store = fresh_store()
        first = ingest_text(store, "---\nid: note.clean\ntitle: Clean\ntype: note\ntags: [x]\n---\n\nbody\n")
        self.assertTrue(first.published, first.validation.errors)
        normalized = first.normalized_path.read_text(encoding="utf-8")
        second = ingest_text(fresh_store(), normalized)
        self.assertEqual(second.frontmatter_conflicts, [])
        self.assertEqual(
            second.normalized_path.read_bytes(),
            first.normalized_path.read_bytes(),
        )



class EveryPublishPathTakesTheGateTests(unittest.TestCase):
    """Verification gap found while checking Batch C end to end (§6).

    ``ingest_text`` passes ``diagnostics=fm.conflicts`` into ``validate``, so
    the gate holds there. The web item-save route does not. It calls
    ``build_item``, which returns the ``MergedFrontmatter`` with its conflicts
    already recorded, keeps that value in ``fm``, and then calls bare
    ``validate(item)`` before ``publish_normalized``.

    So the invariant is enforced on one publish path and merely observable on
    another. A contradicting appended block submitted through the editor
    publishes with HTTP 200, and the conflict is silently discarded -- it is not
    even reported to the caller, because the web route never reads ``fm``.

    The fix is one keyword argument. These tests exercise the route rather than
    the store so they keep holding wherever the gate ends up living.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = TextStrataStore(self.tmp)
        self.app = TextStrataWebApp(workspace_root=Path(self.tmp))
        self.addCleanup(self.app.close)
        handler = create_handler(self.app)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        seeded = ingest_text(self.store, contradicting("id", "note.gate", "note.gate"))
        self.assertTrue(seeded.published, seeded.validation.errors)
        self.original_bytes = seeded.normalized_path.read_bytes()

    def save(self, content):
        request = urllib.request.Request(
            f"{self.base}/api/textstrata/item/note.gate/save",
            data=json.dumps({"content": content}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    # Two stacked blocks disagreeing on a protected key, submitted as an edit.
    PROTECTED_EDIT = (
        "---\nid: note.gate\ntitle: First\ntype: note\ntags: [x]\n---\n"
        "---\ntitle: Second\n---\n\nedited body\n"
    )
    UNPROTECTED_EDIT = (
        "---\nid: note.gate\ntitle: First\ntype: note\ntags: [x]\ncreated_via: tool-a\n---\n"
        "---\ncreated_via: tool-b\n---\n\nedited body\n"
    )

    def test_a_protected_conflict_is_rejected_by_the_web_save_route(self):
        status, body = self.save(self.PROTECTED_EDIT)
        self.assertEqual(status, 400, f"contradicting edit published anyway: {body}")

    def test_a_rejected_web_save_does_not_change_the_normalized_file(self):
        self.save(self.PROTECTED_EDIT)
        self.assertEqual(
            self.store.normalized_path_for_id("note.gate").read_bytes(),
            self.original_bytes,
            "the contradicting edit was published",
        )

    def test_the_rejection_names_the_conflict(self):
        _status, body = self.save(self.PROTECTED_EDIT)
        self.assertIn("title", body)
        self.assertIn("rejected", body)

    def test_an_unprotected_conflict_still_saves(self):
        status, body = self.save(self.UNPROTECTED_EDIT)
        self.assertEqual(status, 200, body)
        self.assertNotEqual(
            self.store.normalized_path_for_id("note.gate").read_bytes(),
            self.original_bytes,
        )

    def test_an_ordinary_edit_is_unaffected(self):
        status, body = self.save("just new body text")
        self.assertEqual(status, 200, body)


if __name__ == "__main__":
    unittest.main()


class RejectionHasNoSideEffectsTests(unittest.TestCase):
    """Verification gap found while checking Task 2 (§6).

    ``web.py`` calls ``save_original(item.id, raw_text)`` at line 969, *before*
    the ``if not result.ok`` check on the next line. So an edit that the gate
    correctly refuses to publish still overwrites the stored original, and
    ``_save_original_bytes`` updates ``original_sha256`` in the same call.

    The result is worse than a stale file. ``verify_original`` compares the
    original against a hash that was rewritten alongside it, so it reports
    ``matches: True`` while the original now holds content that was never
    published and never validated. The one artifact the store promises to keep
    is replaced, and the integrity check that exists to notice that agrees
    nothing is wrong.

    These tests assert only about the rejected path, so they stay correct when
    Task 8 makes ``save_original`` write-once for every path.
    """

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

        seeded = ingest_text(
            self.store,
            "---\nid: note.web\ntitle: First\ntype: note\ntags: [x]\n---\n\noriginal body\n",
        )
        self.assertTrue(seeded.published, seeded.validation.errors)
        self.original = self.store.original_dir / "note.web.md"
        self.original_bytes = self.original.read_bytes()
        self.original_sha = self.store.verify_original("note.web")["expected_sha256"]
        self.normalized_bytes = self.store.normalized_path_for_id("note.web").read_bytes()

    # handling=human_only with preservation=rewrite_allowed is a CONTRADICTORY_POLICY
    # pair, so it fails the gate on its own without relying on conflict routing.
    REJECTED_EDIT = (
        "---\nid: note.web\ntitle: T\ntype: note\ntags: [x]\n"
        "handling: human_only\npreservation: rewrite_allowed\n---\n\nrejected body\n"
    )

    def save(self, content):
        request = urllib.request.Request(
            f"{self.base}/api/textstrata/item/note.web/save",
            data=json.dumps({"content": content}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def test_the_edit_is_actually_rejected(self):
        status, _body = self.save(self.REJECTED_EDIT)
        self.assertEqual(status, 400)
        self.assertEqual(
            self.store.normalized_path_for_id("note.web").read_bytes(),
            self.normalized_bytes,
        )

    def test_a_rejected_edit_does_not_overwrite_the_stored_original(self):
        self.save(self.REJECTED_EDIT)
        self.assertEqual(
            self.original.read_bytes(),
            self.original_bytes,
            "a rejected edit replaced the stored original",
        )

    def test_a_rejected_edit_does_not_rewrite_the_recorded_hash(self):
        self.save(self.REJECTED_EDIT)
        self.assertEqual(
            self.store.verify_original("note.web")["expected_sha256"],
            self.original_sha,
            "the integrity hash was updated to the rejected content",
        )

    def test_verify_original_is_not_fooled_by_a_rejected_edit(self):
        self.save(self.REJECTED_EDIT)
        report = self.store.verify_original("note.web")
        self.assertTrue(report["matches"])
        self.assertEqual(report["actual_sha256"], self.original_sha)
