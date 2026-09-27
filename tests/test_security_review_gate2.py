"""Regressions for the gate 2 independent review (docs/security-review-gate2.md, R1-R5)."""

from __future__ import annotations

import http.client
import ipaddress
import json
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from textstrata import auth
from textstrata.web import BoundedThreadingHTTPServer, TextStrataWebApp, create_handler
from textstrata.web_auth import asset_response_headers, build_security

PASSWORD = "correct horse battery"


class ReviewHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = TextStrataWebApp(self.tmp / "workspace")
        self.security = build_security({"mode": "local", "auth": True}, self.tmp / "state", scrypt_log_n=10)
        self.app.security = self.security
        identity = self.security.identity
        self.admin = identity.bootstrap_admin("admin", PASSWORD)
        member = identity.accept_invite(identity.create_invite(created_by=self.admin.id), "member", PASSWORD)
        self.member_headers = self._headers(member)
        self.admin_headers = self._headers(self.admin)
        self.server = BoundedThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _headers(self, user) -> dict[str, str]:
        token, session = self.security.identity.create_session(user)
        return {"Cookie": f"textstrata_session={token}", "X-CSRF-Token": session.csrf_token}

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            conn.request(method, path, body.encode() if isinstance(body, str) else body, headers or {})
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()


class ActiveUploadIsolationTests(ReviewHarness):
    """R1: uploads never render as active documents in the application origin."""

    def upload(self, filename: str, payload: bytes, part_type: str) -> str:
        body = (f'--b\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {part_type}\r\n\r\n").encode() + payload + b"\r\n--b--\r\n"
        status, _, raw = self.request("POST", "/api/asset/upload", body,
                                      {**self.member_headers, "Content-Type": "multipart/form-data; boundary=b"})
        self.assertEqual(status, 201, raw)
        return json.loads(raw)["url"]

    def test_uploaded_html_and_svg_download_under_sandbox(self):
        script = b"<script>document.title='owned'</script>"
        for filename, part_type, served_type in (
            ("review.html", "text/html", "application/octet-stream"),
            ("review.htm", "text/html", "application/octet-stream"),
            ("review.xhtml", "application/xhtml+xml", "application/octet-stream"),
            ("review.svg", "image/svg+xml", "image/svg+xml"),
            ("review.xml", "text/xml", "application/octet-stream"),
            ("review.js", "text/javascript", "application/octet-stream"),
            ("review", "text/html", "application/octet-stream"),
        ):
            with self.subTest(filename=filename):
                url = self.upload(filename, b"<html>" + script + filename.encode() + b"</html>", part_type)
                for suffix in ("", "?preview=1"):
                    status, headers, _ = self.request("GET", url + suffix, headers=self.admin_headers)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Content-Type"], served_type)
                    self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
                    self.assertIn("sandbox", headers["Content-Security-Policy"])
                    self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_spoofed_image_name_is_served_as_inert_image(self):
        url = self.upload("photo.png", b"<html><script>alert(1)</script></html>", "text/html")
        status, headers, _ = self.request("GET", url, headers=self.admin_headers)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])

    def test_previously_stored_metadata_types_are_neutralized(self):
        assets = self.app.acquisition.assets
        for recorded in ("text/html", "TEXT/HTML; charset=utf-8", "text/html\r\nSet-Cookie: x=1", "image/svg+xml"):
            with self.subTest(recorded=recorded):
                asset = assets.put(b"<script>1</script>" + recorded.encode(), "legacy.bin", media_type=recorded)
                status, headers, _ = self.request("GET", f"/asset/{asset.id}", headers=self.admin_headers)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
                self.assertNotIn("Set-Cookie", headers)

    def test_safe_media_stays_inline(self):
        for media in ("image/png", "image/jpeg", "image/webp", "audio/mpeg", "video/mp4", "text/plain"):
            with self.subTest(media=media):
                headers = dict(asset_response_headers(media, "a" * 64))
                self.assertEqual(headers["Content-Disposition"], "inline")
        self.assertTrue(dict(asset_response_headers("video/vnd+xml", "a" * 64))["Content-Disposition"].startswith("attachment"))


class ProxyIngressTests(ReviewHarness):
    """R2: proxy mode only admits configured proxies that terminated HTTPS."""

    def configure_proxy(self, trusted: str) -> None:
        self.security.mode = "proxy"
        self.security.secure_cookies = True
        self.security.public_url = "https://notes.example.test"
        self.security.trusted_proxies = [ipaddress.ip_network(trusted)]

    def login(self, headers: dict[str, str]):
        return self.request("POST", "/login", urlencode({"username": "admin", "password": PASSWORD}),
                            {"Content-Type": "application/x-www-form-urlencoded", **headers})

    def test_untrusted_direct_peer_cannot_login_or_use_a_session(self):
        self.configure_proxy("192.0.2.10/32")
        for headers in ({}, {"X-Forwarded-Proto": "https"}, {"X-Forwarded-For": "192.0.2.10", "X-Forwarded-Proto": "https"}):
            with self.subTest(headers=headers):
                status, response_headers, raw = self.login(headers)
                self.assertEqual(status, 421, raw)
                self.assertNotIn("Set-Cookie", response_headers)
        for path in ("/api/textstrata/session", "/", "/invite/" + "A" * 43, "/logout"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path, headers={**self.admin_headers, "X-Forwarded-Proto": "https"})
                self.assertEqual(status, 421)
        self.assertEqual(self.request("GET", "/healthz")[0], 200)

    def test_trusted_proxy_must_report_https(self):
        self.configure_proxy("127.0.0.1/32")
        for proto in (None, "http", "http, https", "HTTPS-ish"):
            with self.subTest(proto=proto):
                headers = {"X-Forwarded-Proto": proto} if proto else {}
                self.assertEqual(self.login(headers)[0], 421)
        status, headers, _ = self.login({"X-Forwarded-Proto": "https", "Origin": "https://notes.example.test"})
        self.assertEqual(status, 303)
        self.assertIn("Secure", headers["Set-Cookie"])


class OperatorRouteTests(ReviewHarness):
    """R3: installation operations are administrator-only, including legacy aliases."""

    OPERATOR_ROUTES = [
        ("GET", "/api/textstrata/control/status"), ("GET", "/api/textstrata/control/backup/preview"),
        ("POST", "/api/textstrata/control/backup"), ("POST", "/api/textstrata/control/restore/preview"),
        ("POST", "/api/textstrata/control/restore"), ("GET", "/api/textstrata/setup/status"),
        ("POST", "/api/textstrata/setup/initialize"), ("GET", "/setup"),
        ("POST", "/api/textstrata/settings"), ("POST", "/api/textstrata/restart"),
        ("GET", "/api/acquisition/maintenance/settings"), ("POST", "/api/acquisition/maintenance/settings"),
        ("POST", "/api/acquisition/maintenance/restart"), ("POST", "/api/acquisition/sync"),
        ("POST", "/api/acquisition/queue/clear-completed"), ("POST", "/api/acquisition/trash/empty"),
        ("POST", "/api/acquisition/queue/1/purge-output"), ("POST", "/api/acquisition/channel/x/purge"),
        ("DELETE", "/api/acquisition/queue/1"), ("POST", "/api/textstrata/trash/empty"),
        ("DELETE", "/api/textstrata/trash/x"), ("GET", "/api/parity/queue"), ("POST", "/api/parity/sync"),
        ("POST", "/api/parity/ingest"), ("GET", "/api/textstrata/admin/users"), ("POST", "/api/textstrata/admin/invites"),
    ]

    def test_members_are_denied_every_operator_route_and_alias(self):
        calls = []
        with patch("textstrata.web.control_restore_preview", side_effect=lambda *a: calls.append(a) or {}), \
                patch("textstrata.web.control_backup", side_effect=lambda *a: calls.append(a) or {}), \
                patch("textstrata.web.control_restore", side_effect=lambda *a: calls.append(a) or {}):
            for method, path in self.OPERATOR_ROUTES:
                variants = [path] + ([path.replace("/api/textstrata/", "/api/fabric/", 1)] if "/api/textstrata/" in path else [])
                for variant in variants:
                    with self.subTest(method=method, path=variant):
                        status, _, raw = self.request(method, variant, "{}" if method != "GET" else None, {
                            **self.member_headers, "Content-Type": "application/json", "X-TextStrata-Confirm": "true",
                        })
                        self.assertEqual(status, 403, raw)
                        self.assertEqual(json.loads(raw)["code"], "admin-required")
        self.assertEqual(calls, [])

    def test_members_keep_content_routes(self):
        for method, path in (("GET", "/api/textstrata/settings"), ("GET", "/api/textstrata/graph"),
                             ("GET", "/api/textstrata/trash"), ("GET", "/api/acquisition/queue")):
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, headers=self.member_headers)[0], 200)

    def test_admin_reaches_restore_preview(self):
        with patch("textstrata.web.control_restore_preview", return_value={"ok": True}) as preview:
            status, _, raw = self.request("POST", "/api/fabric/control/restore/preview", '{"source":"/nowhere"}',
                                          {**self.admin_headers, "Content-Type": "application/json"})
        self.assertEqual(status, 200, raw)
        self.assertEqual(preview.call_count, 1)


class PasswordWorkBudgetTests(unittest.TestCase):
    """R4: KDF memory and request threads are bounded before any expensive work."""

    def test_concurrent_verifications_never_exceed_the_kdf_budget(self):
        encoded = auth.hash_password(PASSWORD, log_n=10)
        active, peak, lock = [0], [0], threading.Lock()
        real_scrypt = auth.hashlib.scrypt

        def tracked(*args, **kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            try:
                return real_scrypt(*args, **kwargs)
            finally:
                with lock:
                    active[0] -= 1

        with patch.object(auth.hashlib, "scrypt", side_effect=tracked):
            threads = [threading.Thread(target=auth.verify_password, args=(PASSWORD, encoded)) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertGreaterEqual(peak[0], 1)
        self.assertLessEqual(peak[0], auth.KDF_CONCURRENCY)

    def test_exhausted_budget_fails_fast_without_hashing(self):
        acquired = [auth._KDF_SLOTS.acquire(timeout=5) for _ in range(auth.KDF_CONCURRENCY)]
        self.assertTrue(all(acquired))
        try:
            with patch.object(auth, "KDF_WAIT_SECONDS", 0.01), patch.object(auth.hashlib, "scrypt") as scrypt:
                with self.assertRaises(auth.BusyError):
                    auth.hash_password(PASSWORD, log_n=10)
                scrypt.assert_not_called()
        finally:
            for _ in acquired:
                auth._KDF_SLOTS.release()

    def test_login_returns_503_when_busy(self):
        harness = ReviewHarness()
        harness.setUp()
        try:
            with patch.object(harness.security.identity, "authenticate", side_effect=auth.BusyError()):
                status, headers, _ = harness.request("POST", "/login", urlencode({"username": "admin", "password": PASSWORD}),
                                                     {"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(status, 503)
            self.assertEqual(headers["Retry-After"], "5")
        finally:
            harness.tearDown()

    def test_worker_cap_closes_excess_connections(self):
        release = threading.Event()

        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                release.wait(10)
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                return

        server_class = type("Capped", (BoundedThreadingHTTPServer,), {"max_workers": 1})
        server = server_class(("127.0.0.1", 0), Slow)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            first = socket.create_connection(server.server_address, timeout=5)
            first.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            time.sleep(0.3)
            second = socket.create_connection(server.server_address, timeout=5)
            second.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            self.assertEqual(second.recv(64), b"", "connection over the cap must be closed without work")
            second.close()
            release.set()
            self.assertIn(b"204", first.recv(64))
            first.close()
        finally:
            release.set()
            server.shutdown()
            server.server_close()


class CredentialGenerationTests(unittest.TestCase):
    """R5: session issuance is atomic with the credential state that was verified."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [2_000_000_000]
        self.store = auth.IdentityStore(Path(self.tmp.name) / "identity.sqlite3", clock=lambda: self.now[0], scrypt_log_n=10)
        self.store.bootstrap_admin("admin", "old review password")
        self.store.accept_invite(self.store.create_invite(created_by=None, is_admin=True), "backup-admin", PASSWORD)

    def tearDown(self):
        self.tmp.cleanup()

    def login_across(self, change):
        original = auth.verify_password

        def change_during_verification(password, encoded):
            self.now[0] += 1
            change()
            self.now[0] += 1
            return original(password, encoded)

        with patch.object(auth, "verify_password", side_effect=change_during_verification):
            return self.store.authenticate("admin", "old review password", address="review")

    def test_reset_during_login_blocks_session_issuance(self):
        stale = self.login_across(lambda: self.store.set_password("admin", "new review password"))
        with self.assertRaises(auth.AuthError) as ctx:
            self.store.create_session(stale)
        self.assertEqual(ctx.exception.code, "credentials-changed")

    def test_sign_out_everywhere_and_disable_during_login_block_issuance(self):
        for change in (lambda: self.store.revoke_user_sessions("admin"),
                       lambda: (self.store.set_disabled("admin", True), self.store.set_disabled("admin", False))):
            stale = self.login_across(change)
            with self.subTest(change=change), self.assertRaises(auth.AuthError):
                self.store.create_session(stale)

    def test_generation_bump_invalidates_existing_sessions_without_timestamps(self):
        user = self.store.authenticate("admin", "old review password", address="x")
        token, _ = self.store.create_session(user)
        self.store.revoke_user_sessions("admin")
        self.assertIsNone(self.store.resolve_session(token))
        fresh = self.store.authenticate("admin", "old review password", address="x")
        token, _ = self.store.create_session(fresh)
        self.assertIsNotNone(self.store.resolve_session(token))

    def test_version_one_database_migrates_in_place(self):
        path = Path(self.tmp.name) / "v1.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE schema_meta (version INTEGER NOT NULL); INSERT INTO schema_meta VALUES (1);"
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
                " is_admin INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,"
                " password_changed_at INTEGER NOT NULL);"
                "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf_token TEXT NOT NULL,"
                " created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER);"
            )
            db.execute("INSERT INTO users (username, password_hash, is_admin, created_at, password_changed_at) VALUES (?, ?, 1, 0, 0)",
                       ("legacy", auth.hash_password(PASSWORD, log_n=10)))
        store = auth.IdentityStore(path, scrypt_log_n=10)
        user = store.authenticate("legacy", PASSWORD, address="x")
        token, _ = store.create_session(user)
        self.assertEqual(store.resolve_session(token).user.username, "legacy")
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT version FROM schema_meta").fetchone()[0], auth.SCHEMA_VERSION)


class SchemaMigrationAtomicityTests(unittest.TestCase):
    """R6: an interrupted v1 upgrade rolls back completely and retries cleanly."""

    V1 = (
        "CREATE TABLE schema_meta (version INTEGER NOT NULL); INSERT INTO schema_meta VALUES (1);"
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,"
        " is_admin INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,"
        " password_changed_at INTEGER NOT NULL);"
        "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf_token TEXT NOT NULL,"
        " created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER);"
        "CREATE TABLE invites (token_hash TEXT PRIMARY KEY, created_by INTEGER, is_admin INTEGER NOT NULL DEFAULT 0,"
        " note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER,"
        " used_by INTEGER, revoked_at INTEGER);"
        "CREATE TABLE login_failures (key TEXT PRIMARY KEY, count INTEGER NOT NULL, first_at INTEGER NOT NULL,"
        " locked_until INTEGER NOT NULL DEFAULT 0);"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "identity.sqlite3"
        self.password_hash = auth.hash_password(PASSWORD, log_n=10)
        with sqlite3.connect(self.path) as db:
            db.executescript(self.V1)
            db.execute("INSERT INTO users (username, password_hash, is_admin, created_at, password_changed_at) VALUES (?, ?, 1, 0, 0)",
                       ("legacy", self.password_hash))
            db.execute("INSERT INTO sessions VALUES ('digest', 1, 'csrf', 0, 0, 9999999999, NULL)")
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def state(self) -> tuple:
        with closing_connection(self.path) as db:
            version = db.execute("SELECT version FROM schema_meta").fetchone()[0]
            users = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            sessions = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            counts = (db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                      db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        return version, "auth_generation" in users, "auth_generation" in sessions, counts

    def failing_at(self, marker: str):
        class Failing(sqlite3.Connection):
            def execute(self, sql, *args):
                if marker in sql:
                    raise sqlite3.OperationalError(f"injected failure at {marker}")
                return super().execute(sql, *args)

        return patch.object(auth, "_open_schema_connection",
                            lambda path: sqlite3.connect(path, timeout=10, isolation_level=None, factory=Failing))

    def test_failure_at_each_step_rolls_back_and_retry_succeeds(self):
        for marker in ("ALTER TABLE users", "ALTER TABLE sessions", "UPDATE schema_meta", "COMMIT"):
            with self.subTest(marker=marker):
                with self.failing_at(marker), self.assertRaises(sqlite3.OperationalError):
                    auth.IdentityStore(self.path, scrypt_log_n=10)
                self.assertEqual(self.state(), (1, False, False, (1, 1)), "partial upgrade must roll back")
        store = auth.IdentityStore(self.path, scrypt_log_n=10)
        self.assertEqual(self.state(), (auth.SCHEMA_VERSION, True, True, (1, 1)))
        self.assertEqual(store.authenticate("legacy", PASSWORD, address="x").username, "legacy")

    def test_half_migrated_database_from_the_old_upgrade_is_completed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE users ADD COLUMN auth_generation INTEGER NOT NULL DEFAULT 0")
        db.close()
        self.assertEqual(self.state(), (1, True, False, (1, 1)))
        auth.IdentityStore(self.path, scrypt_log_n=10)
        self.assertEqual(self.state(), (auth.SCHEMA_VERSION, True, True, (1, 1)))

    def test_concurrent_openers_migrate_once(self):
        errors = []
        barrier = threading.Barrier(6)

        def open_store():
            try:
                barrier.wait()
                auth.IdentityStore(self.path, scrypt_log_n=10)
            except Exception as exc:  # noqa: BLE001 - collected for the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=open_store) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.state(), (auth.SCHEMA_VERSION, True, True, (1, 1)))

    def test_unknown_future_version_is_refused_without_changes(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE schema_meta SET version = 99")
        db.close()
        with self.assertRaises(auth.AuthError):
            auth.IdentityStore(self.path, scrypt_log_n=10)
        with closing_connection(self.path) as db:
            self.assertEqual(db.execute("SELECT version FROM schema_meta").fetchone()[0], 99)


def closing_connection(path: Path):
    from contextlib import closing
    return closing(sqlite3.connect(path))


if __name__ == "__main__":
    unittest.main()
