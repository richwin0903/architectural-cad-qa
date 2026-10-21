#!/usr/bin/env python3
"""Initialize local protected runtime configuration using only the standard library."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import secrets
import sys
import warnings
from pathlib import Path


def validate_line(value: str, name: str) -> str:
    if any(character in value for character in ("\n", "\r", "\0")):
        raise ValueError(f"{name} cannot contain line breaks or NUL bytes")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must contain valid Unicode") from None
    return value


def password_hash(password: str) -> str:
    validate_line(password, "Password")
    if len(password) < 12:
        raise ValueError("Administrator password must contain at least 12 characters")
    if len(password) > 1024:
        raise ValueError("Administrator password must contain at most 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def create_config(directory: Path, username: str, password: str, api_key: str) -> Path:
    """Create once with mode 0600; return the path without exposing any values."""
    validate_line(username, "Username")
    validate_line(api_key, "DeepSeek API key")
    username = username.strip()
    if not username or len(username) > 128 or "=" in username:
        raise ValueError(
            "Administrator username must contain 1 to 128 characters and no equals sign"
        )
    encoded = password_hash(password)
    payload = (
        "CAD_ADMIN_USERNAME=" + username + "\n"
        "CAD_ADMIN_PASSWORD_HASH=" + encoded + "\n"
        "DEEPSEEK_API_KEY=" + api_key.strip() + "\n"
    )
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / ".secrets"
    if private.is_symlink():
        raise ValueError("The .secrets directory must not be a symbolic link")
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    target = private / "runtime.env"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def copy_public_config(directory: Path) -> str:
    """Copy the non-secret template once, independently of protected secrets."""
    directory = directory.expanduser().resolve()
    source = directory / ".env.example"
    target = directory / ".env"
    if target.exists() or target.is_symlink():
        return "Existing non-secret .env was preserved."
    if not source.is_file():
        return "No .env.example template was found; Compose defaults apply without a local .env."
    content = source.read_text(encoding="utf-8")
    for line in content.splitlines():
        name = line.strip().split("=", 1)[0]
        if name in {"CAD_ADMIN_PASSWORD", "CAD_ADMIN_PASSWORD_HASH", "DEEPSEEK_API_KEY"}:
            raise ValueError("The .env.example template must contain only non-secret settings")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return "Created non-secret .env from .env.example; review it before starting Compose."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project directory in which .secrets/runtime.env will be created",
    )
    parser.add_argument(
        "--username", default="admin", help="Administrator account name (default: admin)"
    )
    args = parser.parse_args(argv)
    target = args.directory.expanduser().resolve() / ".secrets/runtime.env"
    if target.exists() or target.is_symlink():
        try:
            print(copy_public_config(args.directory))
        except (OSError, ValueError) as error:
            print(f"Non-secret configuration initialization failed: {error}", file=sys.stderr)
            return 1
        print("Protected configuration already exists; it was not changed.")
        return 0
    if not sys.stdin.isatty():
        parser.error(
            "Run this command in an interactive terminal; secret command-line flags are unsupported"
        )
    try:
        # getpass can otherwise fall back to echoing input when terminal controls
        # fail even though stdin reports itself as a TTY.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Administrator password (at least 12 characters): ")
            confirmation = getpass.getpass("Confirm administrator password: ")
            if password != confirmation:
                raise ValueError("Password confirmation does not match")
            api_key = getpass.getpass("DeepSeek API key (Enter to leave unconfigured): ")
        target = create_config(args.directory, args.username, password, api_key)
        public_message = copy_public_config(args.directory)
    except getpass.GetPassWarning:
        print(
            "Hidden input is unavailable in this terminal; no configuration was created.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print(f"Configuration initialization failed: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nConfiguration initialization cancelled.", file=sys.stderr)
        return 1
    print(f"Protected configuration created: {target}")
    print(public_message)
    print("Administrator password was hashed; secret values were not printed.")
    if not api_key.strip():
        print(
            "DeepSeek is unconfigured. Login and CAD previews work; "
            "real model answers require an API key."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
