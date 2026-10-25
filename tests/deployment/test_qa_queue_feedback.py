"""Offline queue, charging and privacy boundaries for multi-user trial QA."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_production_security import ORIGIN, PASSWORD
from test_production_security import deployed as deployed
from test_trial_accounts import provision
from web.backend.admin_auth import COOKIE_NAME
from web.backend.qa_analytics import QAAnalytics
from web.backend.qa_queue import QAQueue


def actor(app, main, context, name):
    token, user = app.state.admin_auth.login(name, PASSWORD, name)
    drawing = main.session_mgr.create(user["uid"])
    drawing.original_path.write_text("private fixture")
    drawing.context = context
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": user["csrf_token"],
        "Cookie": f"{COOKIE_NAME}={token}",
    }
    body = {"request_id": uuid4().hex, "session_id": drawing.session_id, "question": name}
    return user, headers, body


def wait_until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Expected state did not arrive")
        time.sleep(0.01)


@pytest.mark.asyncio
async def test_fifo_queue_capacity_timeout_and_cancel_release():
    queue = QAQueue(concurrency=2, max_waiting=1, wait_seconds=0.03)
    users = [{"uid": str(i), "username": str(i)} for i in range(4)]
    first, second, third = [queue.admit(str(i), users[i]) for i in range(3)]
    assert queue.active == 2 and queue.status("2", users[2])["position"] == 1
    with pytest.raises(HTTPException) as full:
        queue.admit("3", users[3])
    assert full.value.status_code == 429

    async def live():
        return None

    with pytest.raises(HTTPException) as timed_out:
        await queue.wait(third, live)
    assert timed_out.value.status_code == 504
    queue.finish(third, "wait_timeout")
    fourth = queue.admit("3", users[3])
    pending = asyncio.create_task(queue.wait(fourth, live))
    queue.finish(first, "succeeded")
    await pending
    assert fourth.state == "running" and queue.active == 2
    queue.finish(second, "succeeded")
    queue.finish(fourth, "cancelled")
    assert queue.active == 0 and not queue.accounts and not queue.waiting


@pytest.mark.parametrize("action", ["complete", "revoke", "timeout"])
def test_two_running_one_waiting_with_live_ownership_and_charge_after_wait(
    deployed, sample_context, monkeypatch, action
):
    from web.backend.deepseek_qa import DeepSeekQA

    client, app, main = deployed
    auth, _ = provision(app)
    identities = [
        actor(app, main, sample_context, name) for name in ("admin", "trial01", "trial02")
    ]
    release = threading.Event()
    calls = []
    app.state.qa_queue.wait_seconds = 0.15 if action == "timeout" else 3

    async def answer(_self, body, _context):
        calls.append(body.question)
        if body.question != "trial02":
            while not release.is_set():
                await asyncio.sleep(0.01)
        return {"answer": "offline response"}

    monkeypatch.setattr(DeepSeekQA, "answer", answer)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = [
            executor.submit(client.post, "/api/local-qa", headers=h, json=b)
            for _, h, b in identities[:2]
        ]
        wait_until(lambda: len(calls) == 2)
        user, headers, body = identities[2]
        pending = executor.submit(client.post, "/api/local-qa", headers=headers, json=body)
        wait_until(lambda: len(app.state.qa_queue.waiting) == 1)
        try:
            status = client.get(f"/api/qa-status/{body['request_id']}", headers=headers)
            assert status.json()["state"] == "queued" and status.json()["position"] == 1
            assert (
                client.get(
                    f"/api/qa-status/{body['request_id']}", headers=identities[0][1]
                ).status_code
                == 404
            )
            assert (
                client.get("/api/auth/session", headers=headers).json()["usage"][
                    "daily_qa_remaining"
                ]
                == 10
            )
            if action == "revoke":
                auth.revoke(user["uid"])
                assert pending.result(timeout=3).status_code == 401
            elif action == "timeout":
                assert pending.result(timeout=3).status_code == 504
        finally:
            release.set()
        assert all(future.result(timeout=5).status_code == 200 for future in first)
        if action == "complete":
            result = pending.result(timeout=5)
            assert result.status_code == 200 and result.json()["queue_seconds"] > 0
            assert calls == [*calls[:2], "trial02"]
        else:
            assert "trial02" not in calls
        with auth._db() as db:
            used = db.execute(
                "SELECT COALESCE(SUM(qa_count),0) FROM trial_usage WHERE username='trial02'"
            ).fetchone()[0]
        assert used == int(action == "complete")
        assert app.state.qa_queue.active == 0 and not app.state.qa_queue.accounts


def test_feedback_is_owned_csrf_protected_idempotent_and_private(
    deployed, sample_context, monkeypatch
):
    from web.backend.deepseek_qa import DeepSeekQA

    client, app, main = deployed
    auth, _ = provision(app)
    _, headers, body = actor(app, main, sample_context, "trial01")
    _, foreign_headers, _ = actor(app, main, sample_context, "trial02")
    calls = []

    async def answer(*_args):
        calls.append(1)
        return {
            "answer": "ANSWER_MUST_NOT_BE_STORED",
            "performance": {"input_tokens": 123, "output_tokens": 12},
        }

    monkeypatch.setattr(DeepSeekQA, "answer", answer)
    assert client.post("/api/local-qa", headers=headers, json=body).status_code == 200
    assert client.post("/api/local-qa", headers=headers, json=body).status_code == 409
    assert len(calls) == 1
    feedback = {"request_id": body["request_id"], "helpful": True}
    assert client.post("/api/qa-feedback", json=feedback).status_code == 401
    assert (
        client.post(
            "/api/qa-feedback", headers={**headers, "X-CSRF-Token": ""}, json=feedback
        ).status_code
        == 403
    )
    assert (
        client.post("/api/qa-feedback", headers=foreign_headers, json=feedback).status_code == 404
    )
    assert (
        client.post(
            "/api/qa-feedback", headers=headers, json={**feedback, "helpful": "yes"}
        ).status_code
        == 422
    )
    for value in (True, True, False):
        assert (
            client.post(
                "/api/qa-feedback", headers=headers, json={**feedback, "helpful": value}
            ).status_code
            == 200
        )
    analytics = app.state.qa_analytics
    summary = analytics.report()
    assert summary["requests"] == 1 and summary["rated"] == 1 and summary["helpful"] == 0
    assert summary["reported_input_tokens"] == 123
    assert "question" not in json.dumps(summary) and "trial01" not in json.dumps(summary)
    assert analytics.path.stat().st_mode & 0o777 == 0o600
    assert b"ANSWER_MUST_NOT_BE_STORED" not in analytics.path.read_bytes()
    with analytics._db() as db:
        record = db.execute("SELECT * FROM qa_events").fetchone()
        assert record["question"] == body["question"] and record["status"] == "succeeded"
        assert record["processing_seconds"] >= 0 and record["http_status"] == 200
    # Neither relogin nor a new account can vote on a previous login's answer.
    new_token, _ = auth.login("trial01", PASSWORD, "new-login")
    assert new_token
    assert client.post("/api/qa-feedback", headers=headers, json=feedback).status_code == 401


def test_retention_failed_requests_and_restart_reconciliation(
    deployed, sample_context, monkeypatch
):
    from web.backend.deepseek_qa import DeepSeekQA

    client, app, main = deployed
    provision(app)
    user, headers, body = actor(app, main, sample_context, "trial01")

    async def failed(*_args):
        raise HTTPException(502, "PROVIDER_ERROR_BODY_NOT_FOR_ANALYTICS")

    monkeypatch.setattr(DeepSeekQA, "answer", failed)
    assert client.post("/api/local-qa", headers=headers, json=body).status_code == 502
    analytics = app.state.qa_analytics
    assert b"PROVIDER_ERROR_BODY_NOT_FOR_ANALYTICS" not in analytics.path.read_bytes()
    with analytics._db() as db:
        row = db.execute("SELECT * FROM qa_events").fetchone()
        assert row["status"] == "failed" and row["http_status"] == 502
    pending_id = uuid4().hex
    analytics.start(pending_id, user, "pending fixture")
    restarted = QAAnalytics(analytics.path)
    restarted.cleanup(startup=True)
    with restarted._db() as db:
        assert (
            db.execute(
                "SELECT status FROM qa_events WHERE request_id = ?", (pending_id,)
            ).fetchone()[0]
            == "interrupted"
        )
        db.execute("UPDATE qa_events SET created_at = ?", (time.time() - 31 * 86400,))
    assert restarted.report()["requests"] == 0
    restarted.cleanup()
    with restarted._db() as db:
        assert db.execute("SELECT count(*) FROM qa_events").fetchone()[0] == 0
    assert b"pending fixture" not in restarted.path.read_bytes()
