"""Administrator and provisioned trial accounts with revocable opaque sessions.

Only password hashes and SHA-256 session-token digests are persisted. There is no
development-mode or Firebase fallback in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
                CREATE TABLE IF NOT EXISTS trial_accounts (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    activate_by REAL NOT NULL,
                    trial_days INTEGER NOT NULL,
                    activated_at REAL,
                    expires_at REAL,
                    daily_limit INTEGER NOT NULL,
                    total_limit INTEGER NOT NULL,
                    upload_daily_limit INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trial_usage (
                    username TEXT NOT NULL,
                    day TEXT NOT NULL,
                    qa_count INTEGER NOT NULL DEFAULT 0,
                    upload_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (username, day)
                );
                CREATE TABLE IF NOT EXISTS trial_jobs (
                    username TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
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

    @staticmethod
    def _trial_version(account) -> str:
        return hashlib.sha256(
            (account["username"] + "\0" + account["password_hash"]).encode()
        ).hexdigest()

    @staticmethod
    def _day(now: float) -> str:
        return datetime.fromtimestamp(now, timezone(timedelta(hours=8))).date().isoformat()

    def _usage(self, db, account, now):
        today = db.execute(
            "SELECT qa_count, upload_count FROM trial_usage WHERE username = ? AND day = ?",
            (account["username"], self._day(now)),
        ).fetchone()
        total = db.execute(
            "SELECT COALESCE(SUM(qa_count), 0) FROM trial_usage WHERE username = ?",
            (account["username"],),
        ).fetchone()[0]
        return {
            "daily_qa_remaining": max(0, account["daily_limit"] - (today[0] if today else 0)),
            "total_qa_remaining": max(0, account["total_limit"] - total),
            "daily_uploads_remaining": max(
                0, account["upload_daily_limit"] - (today[1] if today else 0)
            ),
            "resets_timezone": "UTC+08:00",
        }

    def _session_user(self, db, row, now):
        if row is None or row["expires_at"] <= now:
            return None
        extra = {"role": "admin"}
        if _text_equal(row["username"], self.config.username):
            version = self._credential_version
        else:
            account = db.execute(
                "SELECT * FROM trial_accounts WHERE username = ?", (row["username"],)
            ).fetchone()
            if not account or not account["enabled"] or not account["expires_at"]:
                return None
            if account["expires_at"] <= now:
                return None
            version = self._trial_version(account)
            extra = {
                "role": "trial",
                "trial_expires_at": account["expires_at"],
                "limits": {
                    "daily_qa": account["daily_limit"],
                    "total_qa": account["total_limit"],
                    "daily_uploads": account["upload_daily_limit"],
                },
                "usage": self._usage(db, account, now),
            }
        if not hmac.compare_digest(row["credential_version"], version):
            return None
        # Drawings belong to this exact login. Accounts never share a drawing UID.
        return {
            "uid": row["token_hash"],
            "username": row["username"],
            "csrf_token": row["csrf"],
            "expires_at": row["expires_at"],
            **extra,
        }

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
            user = self._session_user(db, row, time.time())
            if row is not None and user is None:
                db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (digest,))
        return user

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
            db.execute("BEGIN IMMEDIATE")
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
            is_admin = _text_equal(username, self.config.username)
            account = None
            if not is_admin and re.fullmatch(r"[a-zA-Z0-9_-]{3,32}", username):
                account = db.execute(
                    "SELECT * FROM trial_accounts WHERE username = ?", (username,)
                ).fetchone()
            encoded = account["password_hash"] if account else self.config.password_hash
            # Unknown/disabled accounts perform the same password hash work.
            valid_password = verify_password(password, encoded)
            available = is_admin or (
                account is not None
                and account["enabled"]
                and (
                    account["expires_at"] > now
                    if account["activated_at"] is not None
                    else account["activate_by"] > now
                )
            )
            if not (available and valid_password):
                db.execute("INSERT INTO login_failures VALUES (?, ?)", (peer, now))
                db.commit()
                raise HTTPException(401, "账号或访问密钥错误，或试用已到期/停用。")
            expires_at = now + self.config.ttl
            version = self._credential_version
            if account is not None:
                if account["activated_at"] is None:
                    db.execute(
                        "UPDATE trial_accounts SET activated_at = ?, expires_at = ? "
                        "WHERE username = ?",
                        (now, now + account["trial_days"] * 86400, username),
                    )
                    account = db.execute(
                        "SELECT * FROM trial_accounts WHERE username = ?", (username,)
                    ).fetchone()
                expires_at = min(expires_at, account["expires_at"])
                version = self._trial_version(account)
                # One active login per trial account; quotas survive re-login.
                db.execute("DELETE FROM admin_sessions WHERE username = ?", (username,))
            token = secrets.token_urlsafe(32)
            user = {
                "uid": self.token_digest(token),
                "username": username,
                "csrf_token": secrets.token_urlsafe(32),
                "expires_at": expires_at,
            }
            db.execute(
                "INSERT INTO admin_sessions VALUES (?, ?, ?, ?, ?)",
                (
                    user["uid"],
                    user["csrf_token"],
                    user["username"],
                    user["expires_at"],
                    version,
                ),
            )
            db.execute("DELETE FROM login_failures WHERE peer = ?", (peer,))
            row = db.execute(
                "SELECT * FROM admin_sessions WHERE token_hash = ?", (user["uid"],)
            ).fetchone()
            user = self._session_user(db, row, now)
        return token, user

    def revoke(self, uid: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (uid,))

    def active_uids(self) -> set[str]:
        with self._db() as db:
            now = time.time()
            active = set()
            for row in db.execute("SELECT * FROM admin_sessions").fetchall():
                if self._session_user(db, row, now) is None:
                    db.execute(
                        "DELETE FROM admin_sessions WHERE token_hash = ?", (row["token_hash"],)
                    )
                else:
                    active.add(row["token_hash"])
            db.execute(
                "DELETE FROM login_failures WHERE failed_at < ?",
                (time.time() - self.config.failure_window,),
            )
            db.execute("DELETE FROM trial_jobs WHERE expires_at <= ?", (now,))
            return active

    def reserve_trial_request(self, user: dict, kind: str) -> str | None:
        """Atomically charge a started request and lease one job per account.

        Invalid bodies/ownership and global busy rejection are checked before this.
        Started requests count even on provider failure, bounding retries and costs.
        """
        if kind not in {"qa", "upload"}:
            raise ValueError("Unsupported trial request")
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM admin_sessions WHERE token_hash = ?", (user["uid"],)
            ).fetchone()
            current = self._session_user(db, row, now)
            if current is None:
                raise HTTPException(401, "登录已过期，请重新登录。")
            if current["role"] == "admin":
                return None
            username = current["username"]
            db.execute("DELETE FROM trial_jobs WHERE expires_at <= ?", (now,))
            if db.execute("SELECT 1 FROM trial_jobs WHERE username = ?", (username,)).fetchone():
                raise HTTPException(429, "当前账号已有任务处理中，请等待完成。")
            usage = current["usage"]
            if kind == "qa" and min(usage["daily_qa_remaining"], usage["total_qa_remaining"]) <= 0:
                raise HTTPException(429, "问答额度已用完，请次日重试或联系管理员。")
            if kind == "upload" and usage["daily_uploads_remaining"] <= 0:
                raise HTTPException(429, "今日上传额度已用完，请次日重试。")
            day = self._day(now)
            db.execute(
                "INSERT OR IGNORE INTO trial_usage(username, day) VALUES (?, ?)", (username, day)
            )
            db.execute(
                "UPDATE trial_usage SET qa_count = qa_count + ?, upload_count = upload_count + ? "
                "WHERE username = ? AND day = ?",
                (int(kind == "qa"), int(kind == "upload"), username, day),
            )
            token = secrets.token_urlsafe(24)
            db.execute("INSERT INTO trial_jobs VALUES (?, ?, ?)", (username, token, now + 600))
            return token

    def finish_trial_request(self, lease: str | None) -> None:
        if lease:
            with self._db() as db:
                db.execute("DELETE FROM trial_jobs WHERE token = ?", (lease,))

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
    return {
        key: user[key]
        for key in (
            "username",
            "csrf_token",
            "expires_at",
            "role",
            "trial_expires_at",
            "limits",
            "usage",
        )
        if key in user
    }
