"""Authenticated HTTP boundary: every route is denied without a valid session."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

from textstrata import __version__
from textstrata.presentation.browser_assets import client_asset_path
from textstrata.web import TextStrataWebApp, create_handler, serve
from textstrata.web_auth import WebSecurity, build_security, safe_next
from textstrata.workspace import effective_network, validate_installation_config

PASSWORD = "correct horse battery"
EVIL = "https://evil.example"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Response:
    def __init__(self, raw: http.client.HTTPResponse) -> None:
        self.status = raw.status
        self.headers = raw.headers
        self.body = raw.read().decode("utf-8", errors="replace")

    def json(self) -> dict:
        return json.loads(self.body)


class AuthenticatedWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "workspace"
        self.app = TextStrataWebApp(self.workspace)
        network = {"mode": "local", "host": "127.0.0.1", "port": 0, "auth": True}
        self.app.security = build_security(network, self.tmp / "state", scrypt_log_n=10)
        self.identity = self.app.security.identity
        self.admin = self.identity.bootstrap_admin("admin", PASSWORD)
        self.member = self.identity.accept_invite(self.identity.create_invite(created_by=self.admin.id), "member", PASSWORD)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.app))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method: str, path: str, body: bytes | str | None = None, headers: dict | None = None) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = body.encode("utf-8") if isinstance(body, str) else body
            conn.request(method, path, body=payload, headers=headers or {})
            return Response(conn.getresponse())
        finally:
            conn.close()

    def login(self, username: str = "admin", password: str = PASSWORD, next_path: str = "/") -> Response:
        return self.request(
            "POST", "/login", urlencode({"username": username, "password": password, "next": next_path}),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )

    def session(self, username: str = "admin") -> tuple[dict, str]:
        response = self.login(username)
        self.assertEqual(response.status, 303, response.body)
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        page = self.request("GET", "/", headers={"Cookie": cookie})
        csrf = re.search(r'name="textstrata-csrf" content="([^"]+)"', page.body).group(1)
        return {"Cookie": cookie}, csrf

    def ingested_count(self) -> int:
        return len(list(self.app.store.normalized_paths()))

    def test_anonymous_requests_are_denied_on_every_surface(self):
        asset = client_asset_path("library", __version__)
        cases = [
            ("GET", "/api/textstrata/graph"), ("GET", "/api/fabric/graph"), ("GET", "/api/textstrata/settings"),
            ("GET", "/api/fabric/settings"), ("GET", "/api/acquisition/queue"), ("GET", "/api/textstrata/review"),
            ("GET", "/api/textstrata/trash"), ("GET", "/api/textstrata/session"), ("GET", "/api/textstrata/admin/users"),
            ("GET", asset), ("GET", asset.replace("textstrata-", "fabric-")), ("GET", "/asset/anything.png"),
            ("POST", "/api/ingest"), ("POST", "/api/textstrata/settings"), ("POST", "/api/fabric/settings"),
            ("POST", "/api/asset/upload"), ("POST", "/api/textstrata/restart"), ("POST", "/api/textstrata/admin/invites"),
            ("DELETE", "/api/textstrata/trash/x"), ("DELETE", "/api/acquisition/queue/1"),
        ]
        body = json.dumps({"content": "---\ntitle: Anonymous\n---\nbody"})
        for method, path in cases:
            with self.subTest(method=method, path=path):
                response = self.request(method, path, body if method != "GET" else None,
                                        {"Content-Type": "application/json", "X-TextStrata-Confirm": "true"})
                self.assertEqual(response.status, 401, response.body)
                self.assertEqual(response.json()["code"], "authentication-required")
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.ingested_count(), 0)

    def test_anonymous_pages_redirect_to_login_with_safe_next(self):
        for path in ("/", "/item/system.manual", "/graph", "/new", "/search?q=x", "/whoami"):
            with self.subTest(path=path):
                response = self.request("GET", path)
                self.assertEqual(response.status, 303)
                self.assertTrue(response.headers["Location"].startswith("/login?next="))
                self.assertNotIn("textstrata-csrf", response.body)

    def test_spoofed_identity_headers_do_not_authenticate(self):
        spoofed = {
            "X-Remote-User": "admin", "Remote-User": "admin", "X-Forwarded-User": "admin",
            "X-Forwarded-Email": "admin@example.test", "X-Auth-Request-User": "admin",
            "Authorization": "Basic YWRtaW46Y29ycmVjdCBob3JzZSBiYXR0ZXJ5",
            "X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1", "Forwarded": "for=127.0.0.1",
            "Cookie": "textstrata_session=forged; __Host-textstrata_session=forged",
        }
        self.assertEqual(self.request("GET", "/api/textstrata/graph", headers=spoofed).status, 401)
        self.assertEqual(self.request("POST", "/api/ingest", "{}", {**spoofed, "Content-Type": "application/json"}).status, 401)

    def test_login_sets_hardened_cookie_and_unlocks_pages(self):
        response = self.login(next_path="/graph")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/graph")
        cookie = response.headers["Set-Cookie"]
        for attribute in ("HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(attribute, cookie)
        headers = {"Cookie": cookie.split(";", 1)[0]}
        page = self.request("GET", "/", headers=headers)
        self.assertEqual(page.status, 200)
        self.assertIn('name="textstrata-csrf"', page.body)
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        session = self.request("GET", "/api/textstrata/session", headers=headers).json()
        self.assertEqual(session["user"], {"username": "admin", "is_admin": True})

    def test_login_failures_are_generic_and_open_redirects_are_dropped(self):
        wrong = self.login(password="wrong password!")
        unknown = self.login(username="ghost")
        self.assertEqual((wrong.status, unknown.status), (401, 401))
        self.assertNotIn("Set-Cookie", wrong.headers)
        self.assertIn("Username or password is incorrect", wrong.body)
        self.assertIn("Username or password is incorrect", unknown.body)
        for target in ("//evil.example/x", "https://evil.example", "/\\evil.example", "javascript:alert(1)"):
            with self.subTest(target=target):
                self.assertEqual(self.login(next_path=target).headers["Location"], "/")
        self.assertEqual(safe_next("/item/a?b=c"), "/item/a?b=c")

    def test_repeated_failures_return_429(self):
        for _ in range(5):
            self.login("member", "wrong password!")
        response = self.login("member")
        self.assertEqual(response.status, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)

    def test_writes_require_session_csrf_token_and_same_origin(self):
        headers, csrf = self.session()
        body = json.dumps({"content": "---\ntitle: CSRF probe\ntype: note\n---\n\nBody text for the probe."})
        base = {**headers, "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/ingest", body, base).json()["code"], "csrf-invalid")
        self.assertEqual(self.request("POST", "/api/ingest", body, {**base, "X-CSRF-Token": "forged"}).status, 403)
        cross = self.request("POST", "/api/ingest", body, {**base, "X-CSRF-Token": csrf, "Origin": EVIL})
        self.assertEqual(cross.json()["code"], "cross-origin-denied")
        self.assertEqual(self.ingested_count(), 0)
        accepted = self.request("POST", "/api/ingest", body, {**base, "X-CSRF-Token": csrf, "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(accepted.status, 201, accepted.body)
        self.assertEqual(self.ingested_count(), 1)

    def test_another_users_csrf_token_is_rejected(self):
        admin_headers, _ = self.session("admin")
        _, member_csrf = self.session("member")
        response = self.request("POST", "/api/textstrata/settings", "{}",
                                {**admin_headers, "Content-Type": "application/json", "X-CSRF-Token": member_csrf})
        self.assertEqual(response.json()["code"], "csrf-invalid")

    def test_admin_only_routes_reject_members(self):
        headers, csrf = self.session("member")
        write = {**headers, "Content-Type": "application/json", "X-CSRF-Token": csrf}
        for method, path in (("GET", "/api/textstrata/admin/users"), ("POST", "/api/textstrata/admin/invites"),
                             ("POST", "/api/textstrata/restart"), ("POST", "/api/fabric/restart"),
                             ("POST", "/api/acquisition/maintenance/restart")):
            with self.subTest(path=path):
                response = self.request(method, path, "{}" if method == "POST" else None, write)
                self.assertEqual(response.status, 403)
                self.assertEqual(response.json()["code"], "admin-required")
        self.assertFalse(self.app._restart_requested.is_set())

    def test_admin_invitation_round_trip_is_single_use(self):
        headers, csrf = self.session("admin")
        created = self.request("POST", "/api/textstrata/admin/invites", json.dumps({"ttl_hours": 1}),
                               {**headers, "Content-Type": "application/json", "X-CSRF-Token": csrf})
        self.assertEqual(created.status, 201, created.body)
        path = created.json()["path"]
        self.assertEqual(self.request("GET", path).status, 200)
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        mismatch = self.request("POST", path, urlencode({"username": "newbie", "password": PASSWORD, "confirm": "nope"}), form)
        self.assertEqual(mismatch.status, 400)
        accepted = self.request("POST", path, urlencode({"username": "newbie", "password": PASSWORD, "confirm": PASSWORD}), form)
        self.assertEqual(accepted.status, 303)
        cookie = accepted.headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("GET", "/api/textstrata/session", headers={"Cookie": cookie}).json()["user"]["username"], "newbie")
        self.assertEqual(self.request("GET", path).status, 410)
        again = self.request("POST", path, urlencode({"username": "other", "password": PASSWORD, "confirm": PASSWORD}), form)
        self.assertEqual(again.status, 410)
        self.assertEqual(self.request("GET", "/invite/" + "A" * 43).status, 410)

    def test_cross_origin_login_and_invite_posts_are_refused(self):
        form = {"Content-Type": "application/x-www-form-urlencoded", "Origin": EVIL}
        self.assertEqual(self.request("POST", "/login", urlencode({"username": "admin", "password": PASSWORD}), form).status, 403)

    def test_logout_requires_csrf_and_revokes_session(self):
        headers, csrf = self.session()
        form = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(self.request("POST", "/logout", urlencode({"csrf_token": "forged"}), form).status, 403)
        self.assertEqual(self.request("GET", "/api/textstrata/graph", headers=headers).status, 200)
        response = self.request("POST", "/logout", urlencode({"csrf_token": csrf}), form)
        self.assertEqual(response.status, 303)
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/textstrata/graph", headers=headers).status, 401)

    def test_disabling_an_account_ends_its_live_session(self):
        headers, _ = self.session("member")
        self.assertEqual(self.request("GET", "/api/textstrata/graph", headers=headers).status, 200)
        self.identity.set_disabled("member", True)
        self.assertEqual(self.request("GET", "/api/textstrata/graph", headers=headers).status, 401)

    def test_loopback_health_probe_stays_available(self):
        self.assertEqual(self.request("GET", "/healthz").status, 200)


@unittest.skipUnless(shutil.which("node"), "node is required to execute the browser bootstrap")
class SessionBootstrapScriptTests(unittest.TestCase):
    def test_fetch_wrapper_adds_token_only_to_same_origin_writes(self):
        from textstrata.web_auth import session_bootstrap
        fragment = session_bootstrap("tok\"</script>", "o'brien")
        self.assertNotIn('tok"</script>', fragment.split("<script>", 1)[1].rsplit("</script>", 1)[0])
        script = fragment.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        harness = """
const calls = [];
globalThis.window = globalThis;
globalThis.location = {href: "http://127.0.0.1:9/page", origin: "http://127.0.0.1:9"};
globalThis.document = {addEventListener() {}};
window.fetch = (input, init) => { calls.push([String(input instanceof Request ? input.url : input), init || {}]); return Promise.resolve(); };
""" + script + """
fetch("/api/ingest", {method: "POST", headers: {"Content-Type": "application/json"}});
fetch("/api/textstrata/trash/x", {method: "delete"});
fetch("/api/textstrata/graph");
fetch("https://evil.example/collect", {method: "POST"});
fetch(new Request("http://127.0.0.1:9/api/ingest", {method: "POST"}));
const token = (init) => init.headers instanceof Headers ? init.headers.get("X-CSRF-Token") : null;
console.log(JSON.stringify(calls.map(([url, init]) => [url, token(init), init.headers instanceof Headers ? init.headers.get("Content-Type") : null])));
"""
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(result.stdout)
        expected_token = 'tok"</script>'
        self.assertEqual(calls[0], ["/api/ingest", expected_token, "application/json"])
        self.assertEqual(calls[1][1], expected_token)
        self.assertIsNone(calls[2][1])
        self.assertIsNone(calls[3][1])
        self.assertEqual(calls[4][1], expected_token)


class LocalModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = TextStrataWebApp(self.tmp)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unauthenticated_loopback_rejects_rebound_host_names(self):
        port = self.server.server_address[1]
        for host, expected in ((f"127.0.0.1:{port}", 200), (f"localhost:{port}", 200), (f"[::1]:{port}", 200),
                               ("evil.example", 421), (f"evil.example:{port}", 421), ("", 421)):
            with self.subTest(host=host):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.putrequest("GET", "/api/textstrata/graph", skip_host=True)
                if host:
                    conn.putheader("Host", host)
                conn.endheaders()
                self.assertEqual(conn.getresponse().status, expected)
                conn.close()


class NetworkPolicyTests(unittest.TestCase):
    def config(self, network: dict) -> dict:
        return validate_installation_config({"schema_version": 1, "workspace": "/srv/notes", "network": network})

    def test_lan_modes_require_authentication_and_their_transport_settings(self):
        rejected = [
            {"mode": "local", "host": "0.0.0.0", "port": 8443},
            {"mode": "local", "host": "192.168.1.20", "port": 8443, "auth": True},
            {"mode": "https", "host": "0.0.0.0", "port": 8443},
            {"mode": "https", "host": "0.0.0.0", "port": 8443, "auth": False, "tls": {"cert_file": "/c", "key_file": "/k"}},
            {"mode": "https", "host": "notes.example", "port": 8443, "tls": {"cert_file": "/c", "key_file": "/k"}},
            {"mode": "https", "host": "0.0.0.0", "port": 8443, "tls": {"cert_file": "c", "key_file": "/k"}},
            {"mode": "proxy", "host": "10.0.0.5", "port": 8080, "public_url": "https://notes.example"},
            {"mode": "proxy", "host": "10.0.0.5", "port": 8080, "trusted_proxies": ["10.0.0.1"]},
            {"mode": "proxy", "host": "10.0.0.5", "port": 8080, "trusted_proxies": ["10.0.0.1"], "public_url": "http://notes.example"},
            {"mode": "proxy", "host": "10.0.0.5", "port": 8080, "trusted_proxies": ["not-an-ip"], "public_url": "https://notes.example"},
            {"mode": "proxy", "host": "10.0.0.5", "port": 8080, "trusted_proxies": ["10.0.0.1"], "public_url": "https://notes.example/sub"},
        ]
        for network in rejected:
            with self.subTest(network=network), self.assertRaises(ValueError):
                self.config(network)
        https = self.config({"mode": "https", "host": "0.0.0.0", "port": 8443, "tls": {"cert_file": "/c", "key_file": "/k"}})
        self.assertTrue(https["network"]["auth"])
        proxy = self.config({"mode": "proxy", "host": "10.0.0.5", "port": 8080, "trusted_proxies": ["10.0.0.1"],
                             "public_url": "https://notes.example/"})
        self.assertEqual(proxy["network"]["public_url"], "https://notes.example")
        self.assertEqual(proxy["network"]["trusted_proxies"], ["10.0.0.1/32"])

    def test_environment_host_override_follows_the_configured_mode(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        https = {"network": {"mode": "https", "host": "127.0.0.1", "port": 8443, "tls": {"cert_file": "/c", "key_file": "/k"}}}
        self.assertEqual(effective_network(root, environ={"TEXTSTRATA_HOST": "0.0.0.0"}, installation=https)["host"], "0.0.0.0")
        with self.assertRaises(ValueError):
            effective_network(root, environ={"TEXTSTRATA_HOST": "0.0.0.0"}, installation={})

    def test_forwarded_client_address_only_from_trusted_proxies(self):
        security = WebSecurity(identity=None, mode="proxy", secure_cookies=True, public_url="https://notes.example",
                               trusted_proxies=[ipaddress.ip_network("10.0.0.0/30")])
        self.assertEqual(security.client_address("10.0.0.1", "203.0.113.9, 10.0.0.2"), "203.0.113.9")
        self.assertEqual(security.client_address("192.0.2.4", "203.0.113.9"), "192.0.2.4")
        self.assertEqual(security.client_address("10.0.0.1", "garbage"), "10.0.0.1")
        self.assertTrue(security.origin_allowed("https://notes.example", "10.0.0.5:8080"))
        self.assertFalse(security.origin_allowed(EVIL, "10.0.0.5:8080"))

    def test_tls_cookies_use_host_prefix_and_secure(self):
        security = WebSecurity(identity=None, mode="https", secure_cookies=True)
        cookie = security.session_cookie("token")
        self.assertTrue(cookie.startswith("__Host-textstrata_session=token"))
        self.assertIn("Secure", cookie)
        self.assertEqual(security.session_token("__Host-textstrata_session=abc; other=1"), "abc")
        self.assertEqual(security.session_token("textstrata_session=abc"), "")

    def test_server_refuses_authenticated_mode_without_accounts(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        network = {"mode": "local", "host": "127.0.0.1", "port": _free_port(), "auth": True}
        with self.assertRaisesRegex(ValueError, "bootstrap-admin"):
            serve(root / "ws", network=network, state_dir=root / "state")
        with self.assertRaisesRegex(ValueError, "loopback"):
            serve(root / "ws", network={"mode": "local", "host": "0.0.0.0", "port": 1, "auth": False})


@unittest.skipUnless(shutil.which("openssl"), "openssl CLI is required to mint a test certificate")
class DirectHttpsEndToEndTests(unittest.TestCase):
    """Configure, bootstrap, and serve over TLS through the real CLI."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cert, self.key = self.tmp / "cert.pem", self.tmp / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
             "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(self.key), "-out", str(self.cert)],
            check=True, capture_output=True,
        )
        self.port = _free_port()
        self.env = {**os.environ, "TEXTSTRATA_CONFIG": str(self.tmp / "installation.json"),
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        for name in ("TEXTSTRATA_WORKSPACE", "MARKBASE_WORKSPACE", "FABRIC_ROOT", "TEXTSTRATA_HOST", "FABRIC_HOST",
                     "TEXTSTRATA_PORT", "FABRIC_PORT"):
            self.env.pop(name, None)
        self.process = None

    def tearDown(self):
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process.stdout.close()
            self.process.stderr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "textstrata", *args], env=self.env, input=stdin,
                              capture_output=True, text=True, timeout=60)

    def test_https_mode_serves_only_authenticated_tls(self):
        setup = self.cli("setup", "--non-interactive", "--storage", str(self.tmp / "ws"), "--mode", "https",
                         "--host", "127.0.0.1", "--port", str(self.port), "--tls-cert", str(self.cert),
                         "--tls-key", str(self.key), "--state-dir", str(self.tmp / "state"))
        self.assertEqual(setup.returncode, 0, setup.stderr)
        refused = self.cli("web")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("bootstrap-admin", refused.stderr)
        boot = self.cli("users", "bootstrap-admin", "--username", "owner", "--password-stdin", stdin=PASSWORD + "\n")
        self.assertEqual(boot.returncode, 0, boot.stderr)

        self.process = subprocess.Popen([sys.executable, "-m", "textstrata", "web"], env=self.env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        context = ssl.create_default_context(cafile=str(self.cert))
        deadline = time.monotonic() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline or self.process.poll() is not None:
                    self.fail("server did not start: " + self.process.stderr.read().decode())
                time.sleep(0.2)

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPSConnection("127.0.0.1", self.port, context=context, timeout=10)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                return Response(conn.getresponse())
            finally:
                conn.close()

        anonymous = request("GET", "/api/textstrata/graph")
        self.assertEqual(anonymous.status, 401)
        self.assertIn("max-age", anonymous.headers["Strict-Transport-Security"])
        login = request("POST", "/login", urlencode({"username": "owner", "password": PASSWORD}),
                        {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(login.status, 303)
        cookie = login.headers["Set-Cookie"]
        self.assertTrue(cookie.startswith("__Host-textstrata_session="))
        self.assertIn("Secure", cookie)
        self.assertEqual(request("GET", "/api/textstrata/graph", headers={"Cookie": cookie.split(";", 1)[0]}).status, 200)

        plain = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        with self.assertRaises((http.client.HTTPException, ConnectionError, OSError)):
            plain.request("GET", "/api/textstrata/graph")
            plain.getresponse()
        plain.close()


if __name__ == "__main__":
    unittest.main()
