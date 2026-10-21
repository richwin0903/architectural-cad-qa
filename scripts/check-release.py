#!/usr/bin/env python3
"""Fail closed for accidental secrets/private data in a publication snapshot.

No matching contents are ever printed. --tree uses Git ignore rules to inspect
a clean working copy, --index reads staged blobs, and --archive reads ZIP/TAR
members without extracting them. This is an additional
guard, not proof that every private value or copyright issue has been found.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 95 * 1024 * 1024
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "dist", "htmlcov",
}
REQUIRED = {
    "README.md", "LICENSE", "AGENTS.md", ".gitignore", ".dockerignore",
    ".env.example", "requirements-production.txt", "requirements-dev.txt",
    "pyproject.toml", "Dockerfile", ".github/ci.yml.example",
    "compose.yaml", "compose.local.yaml", "config/Caddyfile.local",
    "config/Caddyfile.example", "THIRD_PARTY_NOTICES.md", "NOTICE",
    "docs/UPSTREAM.md",
    "web/backend/production.py", "web/backend/admin_auth.py",
    "web/backend/production_dwg.py", "web/frontend/package-lock.json",
    "fonts/OFL-NotoSansSC.txt", "web/frontend/public/fonts/LICENSE-OFL.txt",
    "vendor/README.md", "tests/deployment/fixtures/SOURCE.md",
}
# Reviewed public upstream artifacts and generated synthetic examples only.
# Pin binary/archive bytes rather than trusting an extension or path alone.
REVIEWED = {
    "vendor/libredwg-0.14.tar.xz":
        "62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275",
    "vendor/libredwg-0.14.tar.xz.sig":
        "8cf0a0301dbf092161214abe774d1d394c40cf1fcf4c96f97fc363ac73b689a7",
    "vendor/gnu-keyring.gpg":
        "0fe55175b96a8ea1c4b670cc3fe11d56b341e4eff81b161ea49958ae550be6ca",
    "tests/deployment/fixtures/libredwg-line-2018.dxf.gz":
        "1ae1bf79e4ee594ba568ed20f99a6fb5bfd792504059b1f3efc0e56fa8772ada",
    "local_examples/architectural-demo.dwg":
        "d39bfaced9230b18a109754173eb69bd3cf0f154cc979dfd9fd5d9964076a3ef",
    "local_examples/architectural-demo.dxf":
        "33661412430f390a04a9dfd830c993d3fc78501b2305e26f763c6c3c24d5eeb6",
}
DENIED_DIRS = {
    ".secrets", ".local", ".evidence", "uploads", "sessions", "logs",
    "playwright-report", "test-results", ".ssh", "ssh",
} | SKIP_DIRS
DENIED_NAMES = {
    "runtime.env", "initial-admin-password", "credentials", "credentials.json",
    "service-account.json", "serviceaccount.json", "cookies.txt", ".netrc",
    ".npmrc", ".pypirc", ".release.env",
}
DENIED_SUFFIXES = {
    ".log", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12",
    ".pfx", ".bak", ".dump", ".gpg", ".asc", ".pyc", ".pyo",
}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".xz", ".bz2", ".7z", ".rar", ".tgz"}
DRAWING_SUFFIXES = {".dwg", ".dxf", ".pdf", ".rvt", ".ifc"}
PATTERNS = {
    "private-key": re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "provider-token": re.compile(
        rb"\b(?:sk-(?:[a-fA-F0-9]{32}|(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,200})|"
        rb"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}|"
        rb"AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35})\b"
    ),
    "credential-url": re.compile(rb"https?://[^\s/:@]+:[^\s/@]{8,}@"),
    "password-hash": re.compile(rb"\bscrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[a-f0-9]{24,}\$[a-f0-9]{40,}"),
}
ASSIGNMENT = re.compile(
    r"(?:DEEPSEEK_API_KEY|CAD_SESSION_SECRET|CAD_ADMIN_PASSWORD(?:_HASH)?|"
    r"TENCENTCLOUD_SECRET(?:ID|KEY)|AWS_SECRET_ACCESS_KEY)\s*['\"]?\s*[:=]\s*"
    r"['\"]?([^\s'\"#,]+)", re.IGNORECASE,
)
PLACEHOLDERS = {
    "", "none", "null", "false", "true", "changeme", "your-api-key",
    "your_deepseek_api_key", "local-test-placeholder", "test-key", "test-secret",
}


def placeholder(value: str) -> bool:
    """Recognize explicit examples; never exempt a whole test/template file."""
    lower = value.lower()
    if lower in PLACEHOLDERS or lower.startswith(("your_", "your-", "replace_", "replace-")):
        return True
    if re.search(r"(?:^|[-_])(?:test|tests|testing|sentinel|example|dummy|fake)"
                 r"(?:$|[-_])", lower):
        return True
    return bool(re.fullmatch(
        r"(?:sk-(?:proj-)?)?(?:test|fake|dummy|example|placeholder|sample)"
        r"(?:[-_][a-z0-9_-]+)?", lower,
    ))


def plausible_token(value: bytes) -> bool:
    candidate = value.decode("ascii")
    if placeholder(candidate):
        return False
    payload = candidate.rsplit("-", 1)[-1] if candidate.startswith("sk-") else candidate[4:]
    # Obvious repeated synthetic strings are useful test fixtures, not live keys.
    return len(set(payload)) >= 8 and not any(
        payload.lower() == (sequence * (len(payload) // len(sequence) + 1))[:len(payload)]
        for sequence in ("0123456789abcdef", "abcdefghijklmnopqrstuvwxyz", "1234567890")
    )


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True,
    ).stdout


def snapshot(index: bool):
    if index:
        top = Path(os.fsdecode(git("rev-parse", "--show-toplevel")).strip()).resolve()
        if top != ROOT.resolve():
            raise ValueError("index requires a standalone Git repository at this root")
        for entry in git("ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            meta, raw_name = entry.split(b"\t", 1)
            mode, object_id, stage = meta.split()
            name = os.fsdecode(raw_name)
            if mode != b"100644" and mode != b"100755" or stage != b"0":
                yield name, None, "symlink-submodule-or-unmerged-entry"
                continue
            size = int(git("cat-file", "-s", object_id.decode("ascii")))
            if size >= MAX_BYTES:
                yield name, None, "oversize-file"
                continue
            yield name, git("cat-file", "blob", object_id.decode("ascii")), None
    else:
        # An external temporary Git directory honors nested .gitignore files,
        # even in an extracted copy with no .git, without changing its index.
        with tempfile.TemporaryDirectory(prefix="cad-release-index-") as directory:
            subprocess.run(["git", "init", "--bare", "--quiet", directory],
                           check=True, capture_output=True)
            names = subprocess.run(
                ["git", "--git-dir", directory, "--work-tree", str(ROOT),
                 "-c", "core.excludesFile=/dev/null", "ls-files", "--others",
                 "--exclude-standard", "-z"],
                cwd=ROOT, check=True, capture_output=True,
            ).stdout
        for raw_name in names.split(b"\0"):
            if not raw_name:
                continue
            name = os.fsdecode(raw_name)
            if ".git" in PurePosixPath(name).parts:
                continue
            path = ROOT / name
            if path.is_symlink():
                yield name, None, "symlink"
            elif path.stat().st_size >= MAX_BYTES:
                yield name, None, "oversize-file"
            else:
                yield name, path.read_bytes(), None


def archive_snapshot(archive: Path):
    """Inspect archive bytes directly; never extract symlinks or path traversal."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            members = [member for member in handle.infolist() if not member.is_dir()]
            prefix = archive_prefix([member.filename for member in members])
            for member in members:
                name = member.filename.removeprefix(prefix)
                mode = (member.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    yield name, None, "symlink"
                elif member.file_size >= MAX_BYTES:
                    yield name, None, "oversize-file"
                else:
                    yield name, handle.read(member), None
    else:
        with tarfile.open(archive, "r:*") as handle:
            members = [member for member in handle.getmembers() if not member.isdir()]
            prefix = archive_prefix([member.name for member in members])
            for member in members:
                name = member.name.removeprefix(prefix)
                if not member.isfile():
                    yield name, None, "symlink-or-special-file"
                elif member.size >= MAX_BYTES:
                    yield name, None, "oversize-file"
                else:
                    stream = handle.extractfile(member)
                    if stream is None:
                        yield name, None, "unreadable-member"
                    else:
                        with stream:
                            yield name, stream.read(), None


def archive_prefix(names: list[str]) -> str:
    """Accept the single enclosing directory emitted by GitHub source archives."""
    if names and "README.md" not in names:
        directory = names[0].partition("/")[0]
        if directory in {"", ".", ".."} or "\\" in directory:
            return ""
        prefix = directory + "/"
        if prefix + "README.md" in names and all(name.startswith(prefix) for name in names):
            return prefix
    return ""


def inspect(name: str, data: bytes) -> set[str]:
    issues = set()
    path = PurePosixPath(name)
    lower_parts = {part.lower() for part in path.parts}
    filename = path.name.lower()
    if name.startswith(("/", "\\")) or ".." in path.parts or "\\" in name:
        issues.add("unsafe-path")
    if name in REVIEWED:
        if hashlib.sha256(data).hexdigest() != REVIEWED[name]:
            issues.add("reviewed-artifact-checksum-mismatch")
        return issues
    if lower_parts & DENIED_DIRS or filename in DENIED_NAMES:
        issues.add("private-runtime-path")
    if (filename == ".env" or filename.startswith(".env.")) and not filename.endswith(".example"):
        issues.add("environment-file")
    if filename.startswith(("id_rsa", "id_ed25519")) or path.suffix.lower() in DENIED_SUFFIXES:
        issues.add("secret-log-or-database-file")
    if path.suffix.lower() in ARCHIVE_SUFFIXES:
        issues.add("unreviewed-archive")
    if path.suffix.lower() in DRAWING_SUFFIXES:
        issues.add("unreviewed-drawing")
    if data.startswith(b"SQLite format 3\x00"):
        issues.add("database-content")
    for category, pattern in PATTERNS.items():
        if any(category != "provider-token" or plausible_token(match[0])
               for match in pattern.finditer(data)):
            issues.add(category)
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return issues
    for match in ASSIGNMENT.finditer(content):
        value = match.group(1)
        if placeholder(value):
            continue
        if any(char in value for char in "{}[]()$<>") or value.startswith(
            ("os.", "config.", "self.")
        ):
            continue
        if len(value) >= 32 and len(set(value)) >= 12 and (
            re.fullmatch(r"[a-fA-F0-9]{32,}", value)
            or (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value)
                and re.search(r"[0-9]", value))
        ):
            issues.add("literal-server-credential")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tree", action="store_true")
    mode.add_argument("--index", action="store_true")
    mode.add_argument("--archive", type=Path, metavar="PATH")
    args = parser.parse_args()
    seen = set()
    findings = []
    try:
        files = archive_snapshot(args.archive) if args.archive else snapshot(not args.tree)
        for name, data, error in files:
            seen.add(name)
            categories = {error} if error else inspect(name, data)
            findings.extend((name, category) for category in categories)
    except (OSError, ValueError, subprocess.CalledProcessError,
            tarfile.TarError, zipfile.BadZipFile):
        # Never echo subprocess output or file contents on a failure.
        print("FAIL: snapshot-unreadable (use --tree before initializing this repository)")
        return 2
    findings.extend((name, "required-file-missing") for name in REQUIRED - seen)
    for name, category in sorted(findings):
        # repr escapes control characters that may occur in an untrusted filename.
        safe_name = os.fsencode(name)
        for pattern in PATTERNS.values():
            safe_name = pattern.sub(b"[REDACTED]", safe_name)
        print(f"FAIL: {os.fsdecode(safe_name)!r} [{category}]")
    if findings:
        print(f"Publication check failed: {len(findings)} issue(s); contents withheld.")
        return 1
    print(f"Publication check passed: {len(seen)} files; no matching secrets/private artifacts.")
    print("Scope: current snapshot only; manually review licensing and any previous Git history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
