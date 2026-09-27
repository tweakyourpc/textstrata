"""Installation-level identity: accounts, sessions, invitations, throttling.

Credentials use the standard scrypt KDF from the platform OpenSSL build and
session/invite secrets come from :mod:`secrets`. Only SHA-256 digests of bearer
secrets are stored, so a copied database cannot be replayed as a login.

Every account carries an integer ``auth_generation``. Password changes,
disabling, and sign-out-everywhere increment it; sessions record the value they
were issued under and stop resolving once it moves. Session issuance checks the
generation inside the same transaction, so a login that verified an old
password cannot mint a session after the credential changed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 2
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
SESSION_IDLE_SECONDS = 12 * 3600
SESSION_ABSOLUTE_SECONDS = 7 * 24 * 3600
INVITE_DEFAULT_SECONDS = 72 * 3600
USER_FAILURE_LIMIT = 5
ADDRESS_FAILURE_LIMIT = 20
FAILURE_WINDOW_SECONDS = 15 * 60
MAX_LOCK_SECONDS = 15 * 60
# Each production scrypt call needs ~128 MiB. The budget bounds total KDF memory
# per process regardless of how many requests arrive at once.
KDF_CONCURRENCY = 2
KDF_WAIT_SECONDS = 5.0
_SCRYPT_LOG_N = 17
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 256 * 1024 * 1024
_USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
_KDF_SLOTS = threading.BoundedSemaphore(KDF_CONCURRENCY)


class AuthError(Exception):
    """A rejected identity operation with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ThrottledError(AuthError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("login-throttled", "Too many failed sign-in attempts. Try again later.")
        self.retry_after = retry_after


class BusyError(AuthError):
    """The password-hashing budget is exhausted; the caller should retry shortly."""

    retry_after = 5

    def __init__(self) -> None:
        super().__init__("auth-busy", "The server is handling too many sign-ins. Try again in a few seconds.")


@dataclass(frozen=True)
class User:
    id: int
    username: str
    is_admin: bool
    disabled: bool
    auth_generation: int = 0


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str
    expires_at: int


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _scrypt(password: bytes, salt: bytes, *, log_n: int, r: int, p: int, dklen: int) -> bytes:
    if not _KDF_SLOTS.acquire(timeout=KDF_WAIT_SECONDS):
        raise BusyError()
    try:
        return hashlib.scrypt(password, salt=salt, n=2 ** log_n, r=r, p=p, maxmem=_SCRYPT_MAXMEM, dklen=dklen)
    finally:
        _KDF_SLOTS.release()


def hash_password(password: str, *, log_n: int = _SCRYPT_LOG_N) -> str:
    salt = secrets.token_bytes(16)
    derived = _scrypt(password.encode("utf-8"), salt, log_n=log_n, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt$ln={log_n},r={_SCRYPT_R},p={_SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, params, salt, expected = encoded.split("$")
        values = dict(part.split("=", 1) for part in params.split(","))
        log_n, r, p = int(values["ln"]), int(values["r"]), int(values["p"])
        if scheme != "scrypt" or not 10 <= log_n <= 20 or not 1 <= r <= 16 or not 1 <= p <= 4:
            return False
        derived = _scrypt(password.encode("utf-8"), _unb64(salt), log_n=log_n, r=r, p=p, dklen=len(_unb64(expected)))
    except (ValueError, KeyError):
        return False
    return hmac.compare_digest(derived, _unb64(expected))


def normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not 2 <= len(value) <= 64 or set(value) - _USERNAME_CHARS:
        raise AuthError("username-invalid", "Usernames use 2-64 letters, digits, dots, hyphens, or underscores.")
    return value


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError("password-too-short", f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError("password-too-long", f"Passwords are limited to {MAX_PASSWORD_LENGTH} characters.")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    password_changed_at INTEGER NOT NULL,
    auth_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER,
    auth_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id),
    is_admin INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    used_by INTEGER REFERENCES users(id),
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    first_at INTEGER NOT NULL,
    locked_until INTEGER NOT NULL DEFAULT 0
);
"""

_USER_COLUMNS = "id, username, is_admin, disabled, auth_generation"


def _open_schema_connection(path: Path) -> sqlite3.Connection:
    """Autocommit connection for schema work; tests substitute a failing factory."""
    return sqlite3.connect(path, timeout=10, isolation_level=None)


class IdentityStore:
    """SQLite-backed identity state shared by every space in an installation."""

    def __init__(self, path: str | Path, *, clock=time.time, scrypt_log_n: int = _SCRYPT_LOG_N) -> None:
        self.path = Path(path)
        self._clock = clock
        self._log_n = scrypt_log_n
        self._dummy_hash = hash_password(secrets.token_urlsafe(16), log_n=scrypt_log_n)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._initialize_schema()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _initialize_schema(self) -> None:
        """Create or upgrade the schema in one explicit write transaction.

        The connection runs in autocommit mode so Python's sqlite3 module never
        issues an implicit COMMIT; SQLite DDL is transactional, so any failure
        rolls the whole upgrade back. BEGIN IMMEDIATE is taken before the
        version is read, so concurrent openers serialize and the second one
        sees the upgraded version.
        """
        db = _open_schema_connection(self.path)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                row = db.execute("SELECT version FROM schema_meta").fetchone() if "schema_meta" in tables else None
                version = row[0] if row else None
                if version not in (None, 1, SCHEMA_VERSION):
                    raise AuthError("identity-schema-unsupported", f"Unsupported identity schema version {version}.")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                if version == 1:
                    # Version 1 predates credential generations; existing
                    # sessions start at generation 0 alongside their accounts.
                    # Adding only missing columns also completes a v1 database
                    # left half-migrated by an earlier non-atomic upgrade.
                    for table in ("users", "sessions"):
                        columns = {info[1] for info in db.execute(f"PRAGMA table_info({table})")}
                        if "auth_generation" not in columns:
                            db.execute(f"ALTER TABLE {table} ADD COLUMN auth_generation INTEGER NOT NULL DEFAULT 0")
                    db.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))
                elif version is None:
                    db.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _user(row) -> User:
        return User(id=row[0], username=row[1], is_admin=bool(row[2]), disabled=bool(row[3]), auth_generation=row[4])

    def user_count(self) -> int:
        with closing(self._connect()) as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def list_users(self) -> list[User]:
        with closing(self._connect()) as db:
            rows = db.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY username").fetchall()
        return [self._user(row) for row in rows]

    def get_user(self, username: str) -> User:
        with closing(self._connect()) as db:
            row = db.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (normalize_username(username),)).fetchone()
        if row is None:
            raise AuthError("user-not-found", f"No account named {username!r}.")
        return self._user(row)

    def _new_credentials(self, username: str, password: str) -> tuple[str, str]:
        """Validate and hash outside any database transaction."""
        name = normalize_username(username)
        check_password_policy(password)
        return name, hash_password(password, log_n=self._log_n)

    def _insert_user(self, db: sqlite3.Connection, name: str, password_hash: str, *, is_admin: bool) -> User:
        now = self._now()
        try:
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at, password_changed_at) VALUES (?, ?, ?, ?, ?)",
                (name, password_hash, int(is_admin), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise AuthError("username-taken", "That username is already in use.") from exc
        return User(id=cursor.lastrowid, username=name, is_admin=is_admin, disabled=False)

    def bootstrap_admin(self, username: str, password: str) -> User:
        """Create the first administrator. Refused once any account exists."""
        name, password_hash = self._new_credentials(username, password)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise AuthError("already-bootstrapped", "Accounts already exist; invite additional people instead.")
            return self._insert_user(db, name, password_hash, is_admin=True)

    def create_invite(self, *, created_by: int | None, is_admin: bool = False, note: str = "",
                      ttl_seconds: int = INVITE_DEFAULT_SECONDS) -> str:
        if not 60 <= ttl_seconds <= 30 * 24 * 3600:
            raise AuthError("invite-ttl-invalid", "Invitations expire between one minute and thirty days.")
        token = secrets.token_urlsafe(32)
        now = self._now()
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO invites (token_hash, created_by, is_admin, note, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (_digest(token), created_by, int(is_admin), note[:200], now, now + ttl_seconds),
            )
        return token

    def invite_is_open(self, token: str) -> bool:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT expires_at, used_at, revoked_at FROM invites WHERE token_hash = ?", (_digest(token),)
            ).fetchone()
        return bool(row and row[1] is None and row[2] is None and row[0] > self._now())

    def accept_invite(self, token: str, username: str, password: str) -> User:
        if not self.invite_is_open(token):
            raise AuthError("invite-invalid", "This invitation is invalid, expired, or already used.")
        name, password_hash = self._new_credentials(username, password)
        now = self._now()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT is_admin, expires_at, used_at, revoked_at FROM invites WHERE token_hash = ?", (_digest(token),)
            ).fetchone()
            if row is None or row[2] is not None or row[3] is not None or row[1] <= now:
                raise AuthError("invite-invalid", "This invitation is invalid, expired, or already used.")
            user = self._insert_user(db, name, password_hash, is_admin=bool(row[0]))
            db.execute("UPDATE invites SET used_at = ?, used_by = ? WHERE token_hash = ?", (now, user.id, _digest(token)))
        return user

    def _throttle_state(self, db: sqlite3.Connection, key: str, now: int) -> int:
        row = db.execute("SELECT locked_until FROM login_failures WHERE key = ?", (key,)).fetchone()
        return max(0, row[0] - now) if row else 0

    def _record_failure(self, db: sqlite3.Connection, key: str, limit: int, now: int) -> None:
        row = db.execute("SELECT count, first_at FROM login_failures WHERE key = ?", (key,)).fetchone()
        count = 1 if row is None or now - row[1] > FAILURE_WINDOW_SECONDS else row[0] + 1
        first_at = now if count == 1 else row[1]
        locked_until = now + min(MAX_LOCK_SECONDS, 30 * 2 ** (count - limit)) if count >= limit else 0
        db.execute(
            "INSERT INTO login_failures (key, count, first_at, locked_until) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET count = excluded.count, first_at = excluded.first_at, locked_until = excluded.locked_until",
            (key, count, first_at, locked_until),
        )

    def authenticate(self, username: str, password: str, *, address: str) -> User:
        """Verify credentials. The returned user carries the generation that was verified."""
        now = self._now()
        try:
            name = normalize_username(username)
        except AuthError:
            name = ""
        keys = [(f"addr:{address}", ADDRESS_FAILURE_LIMIT)] + ([(f"user:{name}", USER_FAILURE_LIMIT)] if name else [])
        with closing(self._connect()) as db:
            wait = max(self._throttle_state(db, key, now) for key, _ in keys)
            if wait:
                raise ThrottledError(wait)
            row = db.execute(
                f"SELECT {_USER_COLUMNS}, password_hash FROM users WHERE username = ?", (name,)
            ).fetchone()
        valid = verify_password(password[:MAX_PASSWORD_LENGTH], row[5] if row else self._dummy_hash)
        failed = not valid or row is None or bool(row[3])
        # Raise only after the block commits: an exception inside it would
        # roll back the failure counters and silently disable throttling.
        with closing(self._connect()) as db, db:
            if failed:
                for key, limit in keys:
                    self._record_failure(db, key, limit, now)
            else:
                db.execute("DELETE FROM login_failures WHERE key = ?", (f"user:{name}",))
        if failed:
            raise AuthError("login-failed", "Username or password is incorrect.")
        return self._user(row)

    def create_session(self, user: User) -> tuple[str, Session]:
        """Issue a session only if the account still has the generation ``user`` was verified at."""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = self._now()
        expires = now + SESSION_ABSOLUTE_SECONDS
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT disabled, auth_generation FROM users WHERE id = ?", (user.id,)).fetchone()
            if current is None or current[0] or current[1] != user.auth_generation:
                raise AuthError("credentials-changed", "The account changed during sign-in. Sign in again.")
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at, auth_generation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_digest(token), user.id, csrf, now, now, expires, user.auth_generation),
            )
        return token, Session(user=user, csrf_token=csrf, expires_at=expires)

    def resolve_session(self, token: str) -> Session | None:
        if not token or len(token) > 256:
            return None
        now = self._now()
        digest = _digest(token)
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT u.id, u.username, u.is_admin, u.disabled, u.auth_generation, s.csrf_token, s.expires_at, "
                "s.last_seen_at, s.revoked_at, s.auth_generation FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            user = self._user(row)
            if (row[8] is not None or user.disabled or row[6] <= now
                    or now - row[7] > SESSION_IDLE_SECONDS or row[9] != user.auth_generation):
                return None
            if now - row[7] >= 60:
                db.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now, digest))
        return Session(user=user, csrf_token=row[5], expires_at=row[6])

    def revoke_session(self, token: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                       (self._now(), _digest(token)))

    def _bump_generation(self, db: sqlite3.Connection, user_id: int) -> int:
        db.execute("UPDATE users SET auth_generation = auth_generation + 1 WHERE id = ?", (user_id,))
        return db.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                          (self._now(), user_id)).rowcount

    def revoke_user_sessions(self, username: str) -> int:
        user = self.get_user(username)
        with closing(self._connect()) as db, db:
            return self._bump_generation(db, user.id)

    def set_disabled(self, username: str, disabled: bool) -> User:
        user = self.get_user(username)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if disabled and user.is_admin:
                remaining = db.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0 AND id != ?", (user.id,)
                ).fetchone()[0]
                if not remaining:
                    raise AuthError("last-admin", "Refusing to disable the last active administrator.")
            db.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user.id))
            if disabled:
                self._bump_generation(db, user.id)
        return self.get_user(username)

    def set_password(self, username: str, password: str) -> None:
        """Replace a password and invalidate every session, including logins still in flight."""
        user = self.get_user(username)
        _, password_hash = self._new_credentials(user.username, password)
        now = self._now()
        with closing(self._connect()) as db, db:
            db.execute("UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
                       (password_hash, now, user.id))
            self._bump_generation(db, user.id)
            db.execute("DELETE FROM login_failures WHERE key = ?", (f"user:{user.username}",))
