"""Task 8 — a stored original is written once and never replaced.

``save_original`` calls ``_atomic_write`` unconditionally, so re-ingesting a
different body under an existing id silently destroys the first original -- the
one artifact the store promises never to rewrite. The invalid-id fallback name
is derived from ``sha256(item_id)``, so every rejected body sharing one bad id
lands on the same path and overwrites the previous one.

Legitimate update paths must keep working by publishing normalized output only,
never touching the original. Per docs/task8-blast-radius.md those are: web item
save, the file watcher on modify, gateway sync with a changed fingerprint, and
Obsidian import with ``overwrite=True``. The first is already covered by
test_web and test_presentation, which must stay green unchanged. The other three
have no coverage at all and are tested here.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from textstrata.gateway import CompatibilityGateway
from textstrata.ingest import build_item, ingest_text
from textstrata.store import TextStrataStore
from textstrata.vault import import_obsidian_vault


def note(body: str, item_id: str = "note.immutable") -> str:
    return f"---\nid: {item_id}\ntitle: Immutable\ntype: note\ntags: [x]\n---\n\n{body}\n"


def fresh_store():
    return TextStrataStore(tempfile.mkdtemp())


class SaveOriginalIsWriteOnceTests(unittest.TestCase):
    def test_identical_bytes_are_a_no_op(self):
        store = fresh_store()
        first = ingest_text(store, note("same"))
        before = first.original_path.read_bytes()
        second = ingest_text(store, note("same"))
        self.assertTrue(second.published, second.validation.errors)
        self.assertEqual(first.original_path.read_bytes(), before)

    def test_differing_bytes_raise(self):
        store = fresh_store()
        ingest_text(store, note("first"))
        with self.assertRaises(FileExistsError):
            ingest_text(store, note("second"))

    def test_the_original_is_unchanged_after_a_rejected_replacement(self):
        store = fresh_store()
        first = ingest_text(store, note("first"))
        before = first.original_path.read_bytes()
        with self.assertRaises(FileExistsError):
            ingest_text(store, note("second"))
        self.assertEqual(first.original_path.read_bytes(), before)

    def test_normalized_output_is_not_mutated_before_the_raise(self):
        store = fresh_store()
        first = ingest_text(store, note("first"))
        before = first.normalized_path.read_bytes()
        with self.assertRaises(FileExistsError):
            ingest_text(store, note("second"))
        self.assertEqual(
            store.normalized_path_for_id("note.immutable").read_bytes(),
            before,
            "normalized content was published before the original conflict was detected",
        )

    def test_no_revision_is_created_by_a_rejected_replacement(self):
        store = fresh_store()
        ingest_text(store, note("first"))
        with self.assertRaises(FileExistsError):
            ingest_text(store, note("second"))
        self.assertEqual(store.list_revisions("note.immutable"), [])


class InvalidIdFallbackNamesTests(unittest.TestCase):
    """Rejected bodies sharing one bad id must not collide."""

    def test_two_different_bodies_get_different_fallback_names(self):
        store = fresh_store()
        store.ensure_dirs()
        first = store.save_original("Bad Id", "aaa")
        second = store.save_original("Bad Id", "bbb")
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "aaa")
        self.assertEqual(second.read_text(encoding="utf-8"), "bbb")

    def test_identical_bodies_under_a_bad_id_share_one_name(self):
        store = fresh_store()
        store.ensure_dirs()
        self.assertEqual(store.save_original("Bad Id", "aaa"), store.save_original("Bad Id", "aaa"))

    def test_two_rejected_ingests_both_keep_their_originals(self):
        store = fresh_store()
        first = ingest_text(store, "---\nid: Bad Id\ntitle: One\n---\n\nfirst\n")
        second = ingest_text(store, "---\nid: Bad Id\ntitle: Two\n---\n\nsecond\n")
        self.assertFalse(first.published)
        self.assertFalse(second.published)
        self.assertNotEqual(first.original_path, second.original_path)
        self.assertIn("first", first.original_path.read_text(encoding="utf-8"))
        self.assertIn("second", second.original_path.read_text(encoding="utf-8"))


class _MutableUpstream(BaseHTTPRequestHandler):
    """Upstream whose payload the test can change between syncs."""

    markdown = "# Remote Note\n\nfirst revision.\n"
    title = "Remote Note"

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == "/api/library":
            payload = {"items": [{
                "id": "remote-note", "title": type(self).title,
                "path": "notes/remote-note", "source_type": "note",
                "tags": ["remote"], "date_ingested": "2026-07-03",
            }]}
        elif self.path == "/api/item/notes/remote-note":
            payload = {"metadata": {"title": type(self).title}, "markdown": type(self).markdown}
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


class UpdatePathsPreserveTheOriginalTests(unittest.TestCase):
    """The three legitimate update paths with no existing coverage."""

    def test_gateway_sync_with_a_changed_fingerprint(self):
        _MutableUpstream.markdown = "# Remote Note\n\nfirst revision.\n"
        _MutableUpstream.title = "Remote Note"
        server = ThreadingHTTPServer(("127.0.0.1", 0), _MutableUpstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            store = fresh_store()
            gateway = CompatibilityGateway(f"http://127.0.0.1:{server.server_address[1]}")
            self.assertEqual(gateway.sync(store)["imported"], 1)

            item_id = "textstrata.remote-note"
            original = store.original_dir / f"{item_id}.md"
            original_bytes = original.read_bytes()
            normalized_before = store.normalized_path_for_id(item_id).read_bytes()

            # A changed upstream fingerprint must re-import, not skip.
            _MutableUpstream.title = "Remote Note (revised)"
            _MutableUpstream.markdown = "# Remote Note\n\nsecond revision.\n"
            self.assertEqual(gateway.sync(store)["imported"], 1)

            self.assertNotEqual(
                store.normalized_path_for_id(item_id).read_bytes(), normalized_before,
                "changed upstream content did not reach the normalized file",
            )
            self.assertEqual(
                original.read_bytes(), original_bytes,
                "gateway sync rewrote the first stored original",
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_obsidian_import_with_overwrite(self):
        vault = Path(tempfile.mkdtemp())
        page = vault / "Remote Page.md"
        page.write_text("# Remote Page\n\nfirst revision.\n", encoding="utf-8")
        store = fresh_store()

        self.assertEqual(import_obsidian_vault(store, vault)["imported"], 1)
        item_id = next(p.stem for p in store.normalized_paths())
        original = store.original_dir / f"{item_id}.md"
        original_bytes = original.read_bytes()
        normalized_before = store.normalized_path_for_id(item_id).read_bytes()

        page.write_text("# Remote Page\n\nsecond revision.\n", encoding="utf-8")
        self.assertEqual(import_obsidian_vault(store, vault, overwrite=True)["imported"], 1)

        self.assertNotEqual(
            store.normalized_path_for_id(item_id).read_bytes(), normalized_before,
            "overwrite import did not update the normalized file",
        )
        self.assertEqual(
            original.read_bytes(), original_bytes,
            "overwrite import rewrote the first stored original",
        )

    def test_file_watcher_on_modify(self):
        # watchdog is an optional dependency and is not installed. Stub only the
        # two names cmd_watch imports so the real handler code runs unchanged.
        from textstrata import __main__ as cli

        scheduled = []

        class _Observer:
            def __init__(self):
                self._watches = {}

            def schedule(self, handler, path, recursive=False):
                self._watches[path] = handler
                scheduled.append(handler)

            def start(self):
                return None

            def stop(self):
                return None

            def join(self):
                return None

        watchdog = types.ModuleType("watchdog")
        observers = types.ModuleType("watchdog.observers")
        events = types.ModuleType("watchdog.events")
        observers.Observer = _Observer
        events.FileSystemEventHandler = object
        watchdog.observers = observers
        watchdog.events = events

        watched = Path(tempfile.mkdtemp())
        source = watched / "note.watched.md"
        source.write_text(note("first revision", "note.watched"), encoding="utf-8")
        workspace = tempfile.mkdtemp()

        modules = {"watchdog": watchdog, "watchdog.observers": observers, "watchdog.events": events}
        with patch.dict(sys.modules, modules), \
                patch.dict(os.environ, {"TEXTSTRATA_WORKSPACE": workspace}), \
                patch("time.sleep", side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_watch([str(watched)]), 0)
            self.assertTrue(scheduled, "no handler was scheduled")
            handler = scheduled[0]

            created = types.SimpleNamespace(is_directory=False, src_path=str(source))
            handler.on_created(created)

            store = TextStrataStore(workspace)
            original = store.original_dir / "note.watched.md"
            original_bytes = original.read_bytes()
            normalized_before = store.normalized_path_for_id("note.watched").read_bytes()

            source.write_text(note("second revision", "note.watched"), encoding="utf-8")
            handler._debounce.clear()
            handler.on_modified(types.SimpleNamespace(is_directory=False, src_path=str(source)))

        self.assertNotEqual(
            store.normalized_path_for_id("note.watched").read_bytes(), normalized_before,
            "the modified file did not reach the normalized store",
        )
        self.assertEqual(
            original.read_bytes(), original_bytes,
            "the watcher rewrote the first stored original on modify",
        )


class BuildItemIsUnaffectedTests(unittest.TestCase):
    def test_building_an_item_never_touches_the_store(self):
        item, _, _ = build_item(note("first"))
        self.assertEqual(item.id, "note.immutable")


if __name__ == "__main__":
    unittest.main()
