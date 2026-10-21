"""Single administrator authentication; opaque sessions are revocable server side.

Only password hashes and SHA-256 session-token digests are persisted. There is no
development-mode or Firebase fallback in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

COOKIE_NAME = "cad_admin_session"


def _text_equal(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except UnicodeEncodeError:
        return False


def hash_password(password: str) -> str:
    """Generate a portable scrypt hash; never send the plaintext to stdout."""
    if len(password) < 12:
        raise ValueError("Administrator passwords must contain at least 12 characters")
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Administrator passwords must contain valid Unicode") from None
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password_bytes, salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _parse_hash(encoded: str) -> tuple[int, int, int, bytes, bytes]:
    try:
        algorithm, n, r, p, salt, digest = encoded.split("$")
        params = int(n), int(r), int(p), bytes.fromhex(salt), bytes.fromhex(digest)
        if algorithm != "scrypt" or params[:3] != (16384, 8, 1):
            raise ValueError
        if len(params[3]) != 16 or len(params[4]) != 32:
            raise ValueError
        return params
    except (ValueError, TypeError):
        raise ValueError("CAD_ADMIN_PASSWORD_HASH is not a supported scrypt hash") from None


def verify_password(password: str, encoded: str) -> bool:
    n, r, p, salt, expected = _parse_hash(encoded)
    try:
        password_bytes = password.encode("utf-8")
        valid_unicode = True
    except UnicodeEncodeError:
        password_bytes = b""
        valid_unicode = False
    # Preserve the hash work even for malformed JSON Unicode inputs.
    actual = hashlib.scrypt(password_bytes, salt=salt, n=n, r=r, p=p, dklen=32)
    return hmac.compare_digest(actual, expected) and valid_unicode


@dataclass(frozen=True)
class AuthConfig:
    origin: str
    prefix: str
    db_path: Path
    username: str
    password_hash: str
    ttl: int = 8 * 60 * 60
    failure_limit: int = 5
    failure_window: int = 15 * 60

    def __post_init__(self):
        url = urlsplit(self.origin)
        if url.scheme != "https" or not url.netloc or url.path or url.query or url.fragment:
            raise ValueError("CAD_PUBLIC_ORIGIN must be an HTTPS origin without a path")
        if not self.prefix.startswith("/") or self.prefix.endswith("/"):
            raise ValueError("CAD_PUBLIC_PREFIX must be a path such as /cad")
        if not self.username or self.ttl < 1:
            raise ValueError("Administrator username and positive session TTL are required")
        _parse_hash(self.password_hash)

    @classmethod
    def from_env(cls) -> AuthConfig:
        return cls(
            origin=os.environ["CAD_PUBLIC_ORIGIN"].rstrip("/"),
            prefix=os.getenv("CAD_PUBLIC_PREFIX", "/cad"),
            db_path=Path(os.getenv("CAD_AUTH_DB", "/data/auth.sqlite3")),
            username=os.getenv("CAD_ADMIN_USERNAME", "admin"),
            password_hash=os.environ["CAD_ADMIN_PASSWORD_HASH"],
            ttl=int(os.getenv("CAD_SESSION_TTL_SECONDS", "28800")),
        )


class AdminAuth:
    def __init__(self, config: AuthConfig):
        self.config = config
        self._login_lock = threading.Lock()
        self._credential_version = hashlib.sha256(
            (config.username + "\0" + config.password_hash).encode()
        ).hexdigest()
        config.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create with restricted permissions before sqlite opens it.
        fd = os.open(config.db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        config.db_path.chmod(0o600)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf TEXT NOT NULL,
                    username TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    credential_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_failures (
                    peer TEXT NOT NULL, failed_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS failures_time ON login_failures(failed_at);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.config.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def token_digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def require_origin(self, request: Request) -> None:
        # Origin is browser-controlled and mandatory on every mutating request,
        # including login, preventing login CSRF as well as credentialed CSRF.
        if request.headers.get("origin") != self.config.origin:
            raise HTTPException(403, "请求来源不受信任，请从本站登录。")
        if request.headers.get("sec-fetch-site") in {"cross-site", "none"}:
            raise HTTPException(403, "拒绝跨站请求。")

    def authenticate(self, request: Request) -> dict | None:
        token = request.cookies.get(COOKIE_NAME, "")
        if not token or len(token) > 128:
            return None
        digest = self.token_digest(token)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM admin_sessions WHERE token_hash = ?", (digest,)
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= time.time() or not hmac.compare_digest(
                row["credential_version"], self._credential_version
            ):
                db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (digest,))
                return None
        # CAD files belong to this exact login, rather than to a permanent admin
        # UID. Re-login cannot recover drawings from an expired/revoked session.
        return {
            "uid": digest,
            "username": row["username"],
            "csrf_token": row["csrf"],
            "expires_at": row["expires_at"],
        }

    def require_csrf(self, request: Request, user: dict) -> None:
        self.require_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not _text_equal(supplied, user["csrf_token"]):
            raise HTTPException(403, "安全令牌无效，请刷新页面后重试。")

    def login(self, username: str, password: str, peer: str) -> tuple[str, dict]:
        now = time.time()
        # Serializing this small critical section also prevents concurrent
        # password guesses from racing past the failure counter.
        with self._login_lock, self._db() as db:
            db.execute(
                "DELETE FROM login_failures WHERE failed_at < ?",
                (now - self.config.failure_window,),
            )
            failures = db.execute(
                "SELECT count(*) FROM login_failures WHERE peer = ?", (peer,)
            ).fetchone()[0]
            total = db.execute("SELECT count(*) FROM login_failures").fetchone()[0]
            if failures >= self.config.failure_limit or total >= self.config.failure_limit * 4:
                raise HTTPException(
                    429,
                    "登录失败次数过多，请稍后再试。",
                    headers={"Retry-After": str(self.config.failure_window)},
                )
            # Always verify the hash, including unknown usernames.
            valid_password = verify_password(password, self.config.password_hash)
            if not (_text_equal(username, self.config.username) and valid_password):
                db.execute("INSERT INTO login_failures VALUES (?, ?)", (peer, now))
                db.commit()
                raise HTTPException(401, "账号或密码错误。")
            token = secrets.token_urlsafe(32)
            user = {
                "uid": self.token_digest(token),
                "username": self.config.username,
                "csrf_token": secrets.token_urlsafe(32),
                "expires_at": now + self.config.ttl,
            }
            db.execute(
                "INSERT INTO admin_sessions VALUES (?, ?, ?, ?, ?)",
                (
                    user["uid"],
                    user["csrf_token"],
                    user["username"],
                    user["expires_at"],
                    self._credential_version,
                ),
            )
            db.execute("DELETE FROM login_failures WHERE peer = ?", (peer,))
        return token, user

    def revoke(self, uid: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (uid,))

    def active_uids(self) -> set[str]:
        with self._db() as db:
            db.execute(
                "DELETE FROM admin_sessions WHERE expires_at <= ? OR credential_version != ?",
                (time.time(), self._credential_version),
            )
            db.execute(
                "DELETE FROM login_failures WHERE failed_at < ?",
                (time.time() - self.config.failure_window,),
            )
            return {row[0] for row in db.execute("SELECT token_hash FROM admin_sessions")}

    def set_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=self.config.ttl,
            httponly=True,
            secure=True,
            samesite="strict",
            path=self.config.prefix,
        )

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(
            COOKIE_NAME, path=self.config.prefix, httponly=True, secure=True, samesite="strict"
        )


def public_session(user: dict) -> dict:
    return {key: user[key] for key in ("username", "csrf_token", "expires_at")}
