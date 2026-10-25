"""Offline provisioning CLI. Keys are written only to exclusive mode-0600 files.

Generate locally, import hashes through SSH stdin, and manage via server CLI.
There is deliberately no public registration, account-management or key API.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .admin_auth import AdminAuth, AuthConfig, _parse_hash, hash_password


class AccountSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,32}$")
    password_hash: str
    trial_days: int = Field(default=7, ge=1, le=90)
    daily_limit: int = Field(default=10, ge=0, le=1000)
    total_limit: int = Field(default=30, ge=0, le=10000)
    upload_daily_limit: int = Field(default=10, ge=0, le=100)


def import_accounts(auth: AdminAuth, payload: list[dict]) -> int:
    if not isinstance(payload, list) or not 1 <= len(payload) <= 100:
        raise ValueError("Provide 1 to 100 account records")
    specs = [AccountSpec.model_validate(item) for item in payload]
    if len({item.username for item in specs}) != len(specs):
        raise ValueError("Duplicate usernames")
    for spec in specs:
        _parse_hash(spec.password_hash)
        if spec.username == auth.config.username:
            raise ValueError("Administrator username is reserved")
    now = time.time()
    created = 0
    with auth._db() as db:
        db.execute("BEGIN IMMEDIATE")
        for spec in specs:
            existing = db.execute(
                "SELECT * FROM trial_accounts WHERE username = ?", (spec.username,)
            ).fetchone()
            if existing:
                if any(existing[key] != value for key, value in spec.model_dump().items()):
                    raise ValueError("Existing account differs; refusing to overwrite")
                continue  # Idempotent import does not re-enable or renew an account.
            db.execute(
                "INSERT INTO trial_accounts "
                "(username, password_hash, created_at, activate_by, trial_days, "
                "daily_limit, total_limit, upload_daily_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.username,
                    spec.password_hash,
                    now,
                    now + 30 * 86400,
                    spec.trial_days,
                    spec.daily_limit,
                    spec.total_limit,
                    spec.upload_daily_limit,
                ),
            )
            created += 1
    return created


def protected_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)


def generate(directory: Path, count: int) -> None:
    if not 1 <= count <= 100:
        raise ValueError("Account count must be between 1 and 100")
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    directory.chmod(0o700)
    specs = []
    lines = [
        "建筑 CAD 问答试用凭证 — 私密文件，请逐个发放，勿公开整张表。",
        "登录地址：https://wenjunjia.cloud/cad/login",
        "访问 key 填入登录页的“密码 / 访问 key”栏，不是 DeepSeek API Key。",
        "导入后 30 天内首次登录激活，激活后 7 天有效；每天 10 次问答，累计 30 次。",
        "每天 10 次上传，每张最大 25 MB；每账号只保留最近成功上传的图纸。",
        "每天额度按北京时间零点刷新；开始处理即计次（含模型失败），验证失败及繁忙不计。",
        "新登录撤销旧登录。试用账号只访问自己的图纸，不能管理账号、编辑图纸或读取服务器文件。",
        "图纸在服务器解析；相关预览、结构化图元及对话发送到 DeepSeek。请使用有权分享的图纸。",
        "本站保存问题、耗时、成败和有用/没用反馈 30 天，用于改进试用体验。",
        "",
        "账号\t访问 key",
    ]
    for index in range(1, count + 1):
        username = f"trial{index:02d}"
        key = secrets.token_urlsafe(24)
        specs.append(AccountSpec(username=username, password_hash=hash_password(key)).model_dump())
        lines.append(f"{username}\t{key}")
    protected_write(directory / "accounts.txt", "\n".join(lines) + "\n")
    protected_write(directory / "accounts-import.json", json.dumps(specs, indent=2) + "\n")


def update_account(auth: AdminAuth, username: str, **changes) -> None:
    allowed = {"enabled", "daily_limit", "total_limit", "upload_daily_limit", "password_hash"}
    if set(changes) - allowed or not changes:
        raise ValueError("Unsupported or empty account update")
    if "password_hash" in changes:
        _parse_hash(changes["password_hash"])
    for name in {"daily_limit", "total_limit", "upload_daily_limit"} & changes.keys():
        if not isinstance(changes[name], int) or not 0 <= changes[name] <= 10000:
            raise ValueError("Invalid quota")
    if "enabled" in changes and changes["enabled"] not in (0, 1):
        raise ValueError("Invalid account status")
    with auth._db() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute(
            "SELECT 1 FROM trial_accounts WHERE username = ?", (username,)
        ).fetchone():
            raise ValueError("Trial account does not exist")
        queries = {
            "enabled": "UPDATE trial_accounts SET enabled = ? WHERE username = ?",
            "daily_limit": "UPDATE trial_accounts SET daily_limit = ? WHERE username = ?",
            "total_limit": "UPDATE trial_accounts SET total_limit = ? WHERE username = ?",
            "upload_daily_limit": (
                "UPDATE trial_accounts SET upload_daily_limit = ? WHERE username = ?"
            ),
            "password_hash": "UPDATE trial_accounts SET password_hash = ? WHERE username = ?",
        }
        for key, value in changes.items():
            db.execute(queries[key], (value, username))
        if "password_hash" in changes or changes.get("enabled") == 0:
            db.execute("DELETE FROM admin_sessions WHERE username = ?", (username,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("generate")
    create.add_argument("--output-dir", type=Path, required=True)
    create.add_argument("--count", type=int, default=10)
    sub.add_parser("import")
    sub.add_parser("list")
    for command in ("disable", "enable", "rotate"):
        item = sub.add_parser(command)
        item.add_argument("username")
    limits = sub.add_parser("limits")
    limits.add_argument("username")
    limits.add_argument("--daily", type=int, required=True)
    limits.add_argument("--total", type=int, required=True)
    limits.add_argument("--uploads", type=int, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.output_dir, args.count)
        print(f"Created {args.count} credentials in protected local files; keys not printed.")
        return
    auth = AdminAuth(AuthConfig.from_env())
    if args.command == "import":
        raw = sys.stdin.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("Import too large")
        print(f"Imported {import_accounts(auth, json.loads(raw))} new trial accounts.")
    elif args.command == "list":
        with auth._db() as db:
            records = []
            for row in db.execute("SELECT * FROM trial_accounts ORDER BY username").fetchall():
                record = dict(row)
                record.pop("password_hash")
                record["usage"] = auth._usage(db, row, time.time())
                records.append(record)
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        if args.command in {"disable", "enable"}:
            changes = {"enabled": int(args.command == "enable")}
        elif args.command == "rotate":
            changes = {"password_hash": sys.stdin.read(512).strip()}
        else:
            changes = {
                "daily_limit": args.daily,
                "total_limit": args.total,
                "upload_daily_limit": args.uploads,
            }
        update_account(auth, args.username, **changes)
        print("Trial account updated.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        # Validation exceptions can include secret input. Never print their text.
        raise SystemExit(
            "Account operation failed; check inputs, permissions and existing files."
        ) from None
