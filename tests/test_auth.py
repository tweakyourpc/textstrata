"""Identity store contract: accounts, sessions, invitations, throttling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textstrata.auth import (
    SESSION_ABSOLUTE_SECONDS, SESSION_IDLE_SECONDS, AuthError, IdentityStore, ThrottledError,
    hash_password, verify_password,
)

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000

    def __call__(self) -> float:
        return self.now


class IdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.path = Path(self.tmp.name) / "state" / "identity.sqlite3"
        self.store = IdentityStore(self.path, clock=self.clock, scrypt_log_n=10)
        self.admin = self.store.bootstrap_admin("Admin", PASSWORD)

    def tearDown(self):
        self.tmp.cleanup()

    def _member(self, name: str = "member"):
        return self.store.accept_invite(self.store.create_invite(created_by=self.admin.id), name, PASSWORD)

    def test_password_hash_round_trip_and_rejects_tampering(self):
        encoded = hash_password(PASSWORD, log_n=10)
        self.assertTrue(verify_password(PASSWORD, encoded))
        self.assertFalse(verify_password(PASSWORD + "x", encoded))
        self.assertFalse(verify_password(PASSWORD, encoded.replace("scrypt", "plain", 1)))
        self.assertFalse(verify_password(PASSWORD, "garbage"))
        self.assertFalse(verify_password(PASSWORD, encoded.replace("ln=10", "ln=40")))

    def test_bootstrap_only_once_and_normalizes_username(self):
        self.assertEqual(self.admin.username, "admin")
        self.assertTrue(self.admin.is_admin)
        with self.assertRaises(AuthError) as ctx:
            self.store.bootstrap_admin("second", PASSWORD)
        self.assertEqual(ctx.exception.code, "already-bootstrapped")

    def test_password_and_username_policy(self):
        fresh = IdentityStore(Path(self.tmp.name) / "other.sqlite3", scrypt_log_n=10)
        for username, password, code in (
            ("ok-name", "short", "password-too-short"),
            ("ok-name", "x" * 1025, "password-too-long"),
            ("bad name", PASSWORD, "username-invalid"),
            ("a", PASSWORD, "username-invalid"),
        ):
            with self.subTest(code=code), self.assertRaises(AuthError) as ctx:
                fresh.bootstrap_admin(username, password)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(fresh.user_count(), 0)

    def test_authenticate_is_uniform_for_unknown_wrong_and_disabled(self):
        self.assertEqual(self.store.authenticate("ADMIN", PASSWORD, address="10.0.0.1").id, self.admin.id)
        self._member()
        self.store.set_disabled("member", True)
        for username, password in (("nobody", PASSWORD), ("admin", "wrong password!"), ("member", PASSWORD), ("bad name", PASSWORD)):
            with self.subTest(username=username), self.assertRaises(AuthError) as ctx:
                self.store.authenticate(username, password, address="10.0.0.1")
            self.assertEqual(ctx.exception.code, "login-failed")

    def test_account_throttle_blocks_correct_password_until_lock_expires(self):
        for _ in range(5):
            with self.assertRaises(AuthError):
                self.store.authenticate("admin", "wrong password!", address="10.0.0.9")
        with self.assertRaises(ThrottledError) as ctx:
            self.store.authenticate("admin", PASSWORD, address="10.0.0.10")
        self.assertGreater(ctx.exception.retry_after, 0)
        self.clock.now += ctx.exception.retry_after + 1
        self.assertEqual(self.store.authenticate("admin", PASSWORD, address="10.0.0.10").username, "admin")

    def test_address_throttle_spans_usernames(self):
        for index in range(20):
            with self.assertRaises(AuthError):
                self.store.authenticate(f"guess{index}", "wrong password!", address="10.0.0.66")
        with self.assertRaises(ThrottledError):
            self.store.authenticate("admin", PASSWORD, address="10.0.0.66")
        self.assertEqual(self.store.authenticate("admin", PASSWORD, address="10.0.0.67").username, "admin")

    def test_session_lifecycle_idle_absolute_and_revocation(self):
        token, session = self.store.create_session(self.admin)
        self.assertEqual(self.store.resolve_session(token).user.id, self.admin.id)
        self.assertEqual(self.store.resolve_session(token).csrf_token, session.csrf_token)
        self.assertIsNone(self.store.resolve_session(token + "x"))
        self.assertIsNone(self.store.resolve_session(""))
        self.clock.now += SESSION_IDLE_SECONDS + 1
        self.assertIsNone(self.store.resolve_session(token), "idle session must expire")

        token, _ = self.store.create_session(self.admin)
        for _ in range(SESSION_ABSOLUTE_SECONDS // SESSION_IDLE_SECONDS + 1):
            self.clock.now += SESSION_IDLE_SECONDS - 60
            self.store.resolve_session(token)
        self.assertIsNone(self.store.resolve_session(token), "absolute lifetime must cap active sessions")

        token, _ = self.store.create_session(self.admin)
        self.store.revoke_session(token)
        self.assertIsNone(self.store.resolve_session(token))

    def test_password_change_and_disable_sign_out_everywhere(self):
        member = self._member()
        first, _ = self.store.create_session(member)
        second, _ = self.store.create_session(member)
        self.store.set_password("member", "another long passphrase")
        self.assertIsNone(self.store.resolve_session(first))
        self.assertIsNone(self.store.resolve_session(second))
        token, _ = self.store.create_session(self.store.authenticate("member", "another long passphrase", address="x"))
        self.assertIsNotNone(self.store.resolve_session(token))
        self.store.set_disabled("member", True)
        self.assertIsNone(self.store.resolve_session(token))
        self.store.set_disabled("member", False)
        self.assertIsNone(self.store.resolve_session(token), "re-enabling must not resurrect revoked sessions")

    def test_last_admin_cannot_be_disabled(self):
        with self.assertRaises(AuthError) as ctx:
            self.store.set_disabled("admin", True)
        self.assertEqual(ctx.exception.code, "last-admin")

    def test_invites_are_single_use_expiring_and_carry_role(self):
        code = self.store.create_invite(created_by=self.admin.id, is_admin=True)
        self.assertTrue(self.store.invite_is_open(code))
        user = self.store.accept_invite(code, "second-admin", PASSWORD)
        self.assertTrue(user.is_admin)
        self.assertFalse(self.store.invite_is_open(code))
        with self.assertRaises(AuthError):
            self.store.accept_invite(code, "third", PASSWORD)

        expiring = self.store.create_invite(created_by=None, ttl_seconds=3600)
        self.clock.now += 3601
        with self.assertRaises(AuthError):
            self.store.accept_invite(expiring, "late", PASSWORD)
        with self.assertRaises(AuthError):
            self.store.accept_invite("not-a-real-code", "guess", PASSWORD)

    def test_failed_acceptance_leaves_invite_usable(self):
        code = self.store.create_invite(created_by=self.admin.id)
        with self.assertRaises(AuthError):
            self.store.accept_invite(code, "admin", PASSWORD)
        with self.assertRaises(AuthError):
            self.store.accept_invite(code, "newbie", "short")
        self.assertTrue(self.store.invite_is_open(code))
        self.assertFalse(self.store.accept_invite(code, "newbie", PASSWORD).is_admin)

    def test_database_holds_no_bearer_secrets_or_plaintext_passwords(self):
        token, session = self.store.create_session(self.admin)
        code = self.store.create_invite(created_by=self.admin.id)
        raw = b"".join(path.read_bytes() for path in self.path.parent.iterdir())
        for secret in (token, code, PASSWORD):
            self.assertNotIn(secret.encode(), raw)
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
