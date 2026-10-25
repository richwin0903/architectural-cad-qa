"""Private 30-day QA evaluation records. No answers, files, cookies or provider secrets."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

RETENTION_DAYS = 30


class QAAnalytics:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS qa_events (
                    request_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    owner_uid TEXT NOT NULL,
                    role TEXT NOT NULL,
                    question TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    queue_seconds REAL NOT NULL DEFAULT 0,
                    processing_seconds REAL NOT NULL DEFAULT 0,
                    http_status INTEGER,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    helpful INTEGER,
                    feedback_at REAL
                );
                CREATE INDEX IF NOT EXISTS qa_events_created ON qa_events(created_at);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA secure_delete = ON")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def cutoff():
        return time.time() - RETENTION_DAYS * 86400

    def cleanup(self, *, startup=False):
        with self._db() as db:
            db.execute("DELETE FROM qa_events WHERE created_at < ?", (self.cutoff(),))
            if startup:
                db.execute(
                    "UPDATE qa_events SET status = 'interrupted' "
                    "WHERE status IN ('queued', 'running')"
                )

    def start(self, request_id, user, question):
        try:
            with self._db() as db:
                db.execute(
                    "INSERT INTO qa_events "
                    "(request_id, username, owner_uid, role, question, created_at, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'queued')",
                    (
                        request_id,
                        user["username"],
                        user["uid"],
                        user["role"],
                        question[:4000],
                        time.time(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "这个请求已提交，请勿重复发送。") from None

    def running(self, request_id, queue_seconds):
        with self._db() as db:
            db.execute(
                "UPDATE qa_events SET status = 'running', queue_seconds = ? WHERE request_id = ?",
                (queue_seconds, request_id),
            )

    def finish(self, request_id, status, queue_seconds, processing_seconds, code, performance=None):
        performance = performance or {}
        with self._db() as db:
            db.execute(
                "UPDATE qa_events SET status = ?, queue_seconds = ?, processing_seconds = ?, "
                "http_status = ?, input_tokens = ?, output_tokens = ? WHERE request_id = ?",
                (
                    status,
                    round(queue_seconds, 3),
                    round(processing_seconds, 3),
                    code,
                    performance.get("input_tokens"),
                    performance.get("output_tokens"),
                    request_id,
                ),
            )

    def feedback(self, request_id, user, helpful):
        with self._db() as db:
            updated = db.execute(
                "UPDATE qa_events SET helpful = ?, feedback_at = ? "
                "WHERE request_id = ? AND owner_uid = ? AND status = 'succeeded' "
                "AND created_at >= ?",
                (int(helpful), time.time(), request_id, user["uid"], self.cutoff()),
            )
            if updated.rowcount != 1:
                raise HTTPException(404, "回答不存在、已过期或不属于当前登录。")
        return {"helpful": helpful}

    def report(self, *, include_questions=False):
        # CLI only. Default summary excludes question text and account identifiers.
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM qa_events WHERE created_at >= ? AND role = 'trial' "
                "ORDER BY created_at DESC",
                (self.cutoff(),),
            ).fetchall()
        successful = [r for r in rows if r["status"] == "succeeded"]
        rated = [r for r in successful if r["helpful"] is not None]
        completed = [r for r in rows if r["status"] not in {"queued", "running"}]
        result = {
            "retention_days": RETENTION_DAYS,
            "trial_accounts_with_requests": len({r["username"] for r in rows}),
            "requests": len(rows),
            "completed": len(completed),
            "succeeded": len(successful),
            "rated": len(rated),
            "helpful": sum(r["helpful"] for r in rated),
            "helpful_rate_among_rated": round(sum(r["helpful"] for r in rated) / len(rated), 3)
            if rated
            else None,
            "average_queue_seconds": round(sum(r["queue_seconds"] for r in rows) / len(rows), 2)
            if rows
            else None,
            "average_success_processing_seconds": round(
                sum(r["processing_seconds"] for r in successful) / len(successful), 2
            )
            if successful
            else None,
            "reported_input_tokens": sum(r["input_tokens"] or 0 for r in rows),
            "reported_output_tokens": sum(r["output_tokens"] or 0 for r in rows),
            "note": "反馈有用率不是准确率；token 仅统计模型已报告值，不能代替供应商账单。",
        }
        if include_questions:
            result["records"] = [
                {
                    k: r[k]
                    for k in (
                        "request_id",
                        "username",
                        "question",
                        "created_at",
                        "status",
                        "queue_seconds",
                        "processing_seconds",
                        "http_status",
                        "helpful",
                    )
                }
                for r in rows
            ]
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", action="store_true", help="Include private question text")
    parser.add_argument("--output", type=Path, help="Write an exclusive private JSON file")
    args = parser.parse_args()
    if args.questions and not args.output:
        parser.error("Question export requires --output; do not print private questions to stdout")
    path = Path(os.getenv("CAD_AUTH_DB", "/data/auth.sqlite3")).with_name("qa-events.sqlite3")
    analytics = QAAnalytics(path)
    analytics.cleanup()
    report = json.dumps(
        analytics.report(include_questions=args.questions), ensure_ascii=False, indent=2
    )
    if args.output:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(report + "\n")
        print("Private evaluation export saved.")
    else:
        print(report)


if __name__ == "__main__":
    main()
