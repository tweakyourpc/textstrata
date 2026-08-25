"""Task 14 — publication must be serialized per item across processes.

Nothing on the publish path takes a lock. Two processes ingesting the same id
interleave freely: both build an item, both call ``publish_normalized``, and the
second ``os.replace`` wins. The rename is atomic, so no file is corrupted, but
one process's content is silently discarded and neither is told. The revision
snapshot taken from the file mid-flight can belong to either writer.

§4 adds ``item_lock(item_id)``, a context manager holding a per-item POSIX
advisory lock under ``.fabric/locks/``.

Two properties of that mechanism shape these tests.

POSIX advisory locks (``flock``) are held per *open file description*, not per
process. A descriptor inherited across ``fork`` shares one description, so a
forked child would not block on a lock its parent holds. A child that opens the
lock file itself gets its own description and does block, whether it was forked
or spawned -- so the hazard depends on exactly where the open happens, which is
an implementation detail these tests should not depend on. Every cross-process
test here uses the ``spawn`` start method, which gives the child a fresh
interpreter with no inherited descriptors and no shared module state. That is
also what two genuinely separate CLI or server processes look like, so the
tests measure the guarantee the store actually has to provide.

On Windows the same guarantee needs a different primitive (``msvcrt.locking``
or a named mutex) and only single-writer semantics are available -- no shared
read locks, and no upgrade path. These tests assert the POSIX behaviour and
deliberately do not branch or skip: if the suite is ever run on Windows, the
locking implementation is what needs the platform branch, not this file. A
skipped test here would report a guarantee nobody checked.
"""

import multiprocessing
import os
import tempfile
import time
import unittest

from textstrata.ingest import build_item
from textstrata.store import TextStrataStore


ACQUIRE_TIMEOUT = 3.0
BLOCKED_GRACE = 1.0


def item_text(body: str, item_id: str = "note.locked") -> str:
    return f"---\nid: {item_id}\ntitle: Locked\ntype: note\ntags: [x]\n---\n\n{body}\n"


# --- child entry points; must be importable at module level for spawn ---

def _child_take_lock(root, item_id, hold, queue):
    from textstrata.store import TextStrataStore as Store
    try:
        store = Store(root)
        store.ensure_dirs()
        with store.item_lock(item_id):
            queue.put("acquired")
            time.sleep(hold)
        queue.put("released")
    except Exception as exc:  # surfaced so a missing API is never a silent pass
        queue.put(f"error:{type(exc).__name__}:{exc}")


def _child_ingest(root, text, queue):
    from textstrata.ingest import ingest_text
    from textstrata.store import TextStrataStore as Store
    try:
        store = Store(root)
        result = ingest_text(store, text)
        queue.put("published" if result.published else "rejected")
    except Exception as exc:
        queue.put(f"error:{type(exc).__name__}:{exc}")


def _child_trash(root, item_id, queue):
    from textstrata.store import TextStrataStore as Store
    try:
        Store(root).trash_item(item_id)
        queue.put("trashed")
    except Exception as exc:
        queue.put(f"error:{type(exc).__name__}:{exc}")


def _child_restore(root, trash_name, queue):
    from textstrata.store import TextStrataStore as Store
    try:
        Store(root).restore_trash(trash_name)
        queue.put("restored")
    except Exception as exc:
        queue.put(f"error:{type(exc).__name__}:{exc}")


class _CrossProcessCase(unittest.TestCase):
    def setUp(self):
        self.ctx = multiprocessing.get_context("spawn")
        self.root = tempfile.mkdtemp()
        self.store = TextStrataStore(self.root)
        self.store.ensure_dirs()

    def spawn(self, target, *args):
        queue = self.ctx.Queue()
        process = self.ctx.Process(target=target, args=(*args, queue))
        process.daemon = True
        process.start()
        self.addCleanup(self._reap, process)
        return process, queue

    def _reap(self, process):
        if process.is_alive():
            process.terminate()
        process.join(timeout=ACQUIRE_TIMEOUT)

    def assertBlocked(self, queue, what):
        time.sleep(BLOCKED_GRACE)
        # Do NOT put queue.get() in an assertTrue message: the message is built
        # eagerly, so it would drain an empty queue and block forever, making
        # this assertion unpassable no matter what the implementation does.
        if not queue.empty():
            self.fail(f"{what} completed while the item lock was held: {queue.get()}")

    def drain(self, queue, timeout=ACQUIRE_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not queue.empty():
                return queue.get()
            time.sleep(0.05)
        return None


class ItemLockExcludesOtherProcessesTests(_CrossProcessCase):
    def test_a_second_process_cannot_take_the_same_item_lock(self):
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_take_lock, self.root, "note.locked", 0.0)
            self.assertBlocked(queue, "a second process")

    def test_the_second_process_proceeds_once_the_lock_is_released(self):
        _, queue = None, None
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_take_lock, self.root, "note.locked", 0.0)
            self.assertBlocked(queue, "a second process")
        self.assertEqual(self.drain(queue), "acquired")

    def test_a_different_item_is_not_blocked(self):
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_take_lock, self.root, "note.other", 0.0)
            self.assertEqual(self.drain(queue), "acquired")

    def test_the_lock_lives_under_the_metadata_directory(self):
        with self.store.item_lock("note.locked"):
            locks = self.store.metadata_dir / "locks"
            self.assertTrue(locks.is_dir())
            self.assertTrue(any(locks.iterdir()))

    def test_the_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with self.store.item_lock("note.locked"):
                raise RuntimeError("boom")
        _, queue = self.spawn(_child_take_lock, self.root, "note.locked", 0.0)
        self.assertEqual(self.drain(queue), "acquired")


class PublicationTakesTheLockTests(_CrossProcessCase):
    def test_ingest_blocks_while_the_item_lock_is_held(self):
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_ingest, self.root, item_text("from the child"))
            self.assertBlocked(queue, "a concurrent ingest")

    def test_ingest_completes_once_the_lock_is_released(self):
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_ingest, self.root, item_text("from the child"))
            self.assertBlocked(queue, "a concurrent ingest")
        self.assertEqual(self.drain(queue), "published")

    def test_direct_normalized_publication_blocks_too(self):
        item, _, _ = build_item(item_text("first"))
        self.store.publish_normalized(item)
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_ingest, self.root, item_text("first"))
            self.assertBlocked(queue, "a concurrent publication")


class TrashBoundariesTakeTheLockTests(_CrossProcessCase):
    def setUp(self):
        super().setUp()
        item, _, _ = build_item(item_text("first"))
        self.store.publish_normalized(item)
        self.store.save_original("note.locked", item_text("first"))

    def test_trash_blocks_while_the_item_lock_is_held(self):
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_trash, self.root, "note.locked")
            self.assertBlocked(queue, "a concurrent trash")

    def test_restore_blocks_while_the_item_lock_is_held(self):
        deleted = self.store.trash_item("note.locked")
        with self.store.item_lock("note.locked"):
            _, queue = self.spawn(_child_restore, self.root, deleted["trash_name"])
            self.assertBlocked(queue, "a concurrent restore")


class NestedWritesDoNotDeadlockTests(unittest.TestCase):
    """Internal writes under an outer lock must use unlocked helpers."""

    def setUp(self):
        self.store = TextStrataStore(tempfile.mkdtemp())
        self.store.ensure_dirs()

    def _with_deadline(self, action, seconds=5.0):
        """Run in a thread so a deadlock fails the test instead of hanging it."""
        import threading

        outcome = {}

        def run():
            try:
                outcome["value"] = action()
            except BaseException as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(seconds)
        self.assertFalse(thread.is_alive(), "deadlocked while holding the item lock")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def test_publishing_inside_an_outer_lock_does_not_deadlock(self):
        item, _, _ = build_item(item_text("first"))

        def action():
            with self.store.item_lock("note.locked"):
                return self.store.publish_normalized(item)

        self.assertTrue(self._with_deadline(action).exists())

    def test_ingesting_inside_an_outer_lock_does_not_deadlock(self):
        from textstrata.ingest import ingest_text

        def action():
            with self.store.item_lock("note.locked"):
                return ingest_text(self.store, item_text("first"))

        self.assertTrue(self._with_deadline(action).published)

    def test_taking_the_same_item_lock_twice_in_one_process_does_not_deadlock(self):
        def action():
            with self.store.item_lock("note.locked"):
                with self.store.item_lock("note.locked"):
                    return True

        self.assertTrue(self._with_deadline(action))

    def test_trashing_inside_an_outer_lock_does_not_deadlock(self):
        item, _, _ = build_item(item_text("first"))
        self.store.publish_normalized(item)

        def action():
            with self.store.item_lock("note.locked"):
                return self.store.trash_item("note.locked")

        self.assertEqual(self._with_deadline(action)["item_id"], "note.locked")


class LockFilesAreNotContentTests(unittest.TestCase):
    def test_lock_files_do_not_appear_as_items(self):
        store = TextStrataStore(tempfile.mkdtemp())
        store.ensure_dirs()
        with store.item_lock("note.locked"):
            pass
        self.assertEqual(store.normalized_paths(), [])
        self.assertNotIn("locks", [p.name for p in store.normalized_dir.iterdir()])


if __name__ == "__main__":
    unittest.main()
