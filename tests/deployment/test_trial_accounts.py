"""Trial permissions, cost limits, isolation and provisioning without paid model calls."""

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from test_production_security import ORIGIN, PASSWORD, PREFIX, login
from test_production_security import deployed as deployed
from web.backend.admin_auth import COOKIE_NAME, AdminAuth, hash_password
from web.backend.trial_accounts import generate, import_accounts, update_account


def provision(app, **limits):
    auth = app.state.admin_auth
    specs = [
        {"username": name, "password_hash": hash_password(PASSWORD), **limits}
        for name in ("trial01", "trial02")
    ]
    assert import_accounts(auth, specs) == 2
    return auth, specs


def signed_in(client, name="trial01"):
    response = login(client, username=name)
    assert response.status_code == 200
    return response.json(), {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def drawing_for(client, auth, main, context):
    drawing = main.session_mgr.create(auth.token_digest(client.cookies.get(COOKIE_NAME)))
    drawing.original_path.write_text("private drawing")
    drawing.context = context
    return drawing


def test_provisioned_keys_are_exclusive_private_files_and_import_is_atomic(deployed, tmp_path):
    _, app, _ = deployed
    directory = tmp_path / "credentials"
    generate(directory, 10)
    assert directory.stat().st_mode & 0o777 == 0o700
    for path in directory.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        generate(directory, 10)
    records = json.loads((directory / "accounts-import.json").read_text())
    assert len(records) == 10 and len({record["password_hash"] for record in records}) == 10
    auth = app.state.admin_auth
    assert import_accounts(auth, records) == 10
    assert import_accounts(auth, records) == 0
    with pytest.raises(ValueError):
        import_accounts(auth, [{**records[0], "username": "admin"}])
    with pytest.raises(ValueError):
        import_accounts(
            auth,
            [
                {**records[0], "username": "new-trial"},
                {**records[0], "daily_limit": 9},
            ],
        )
    with auth._db() as db:
        assert db.execute("SELECT count(*) FROM trial_accounts").fetchone()[0] == 10


def test_activation_role_single_login_and_admin_stays_valid(deployed):
    client, app, _ = deployed
    auth, _ = provision(app)
    admin_token, admin = auth.login("admin", PASSWORD, "admin-peer")
    assert admin_token and admin["role"] == "admin"
    session, _ = signed_in(client)
    old_cookie = client.cookies.get(COOKIE_NAME)
    assert session["role"] == "trial"
    assert 6.99 * 86400 < session["trial_expires_at"] - time.time() < 7.01 * 86400
    assert "password_hash" not in session and "uid" not in session
    again, _ = signed_in(client)
    assert again["trial_expires_at"] == session["trial_expires_at"]
    assert admin["uid"] in auth.active_uids()
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, old_cookie, path=PREFIX)
    assert client.get("/api/auth/session").status_code == 401
    # User input cannot grant administrator privileges.
    response = client.post(
        "/api/auth/login",
        headers={"Origin": ORIGIN},
        json={
            "username": "trial01",
            "password": PASSWORD,
            "role": "admin",
            "daily_limit": 999,
        },
    )
    assert response.json()["role"] == "trial"
    assert response.json()["limits"]["daily_qa"] == 10


@pytest.mark.parametrize("action", ["disable", "expire", "rotate"])
def test_account_changes_revoke_cookie_immediately(deployed, action):
    client, app, _ = deployed
    auth, _ = provision(app)
    signed_in(client)
    if action == "disable":
        update_account(auth, "trial01", enabled=0)
    elif action == "rotate":
        update_account(auth, "trial01", password_hash=hash_password("replacement-key-for-test"))
    else:
        with auth._db() as db:
            db.execute("UPDATE trial_accounts SET expires_at = ?", (time.time() - 1,))
    assert client.get("/api/auth/session").status_code == 401
    assert login(client, username="trial01").status_code == 401
    assert login(client).status_code == 200


def test_unused_activation_deadline_is_enforced(deployed):
    client, app, _ = deployed
    auth, _ = provision(app)
    with auth._db() as db:
        db.execute("UPDATE trial_accounts SET activate_by = ?", (time.time() - 1,))
    assert login(client, username="trial01").status_code == 401


def test_trial_drawings_cannot_cross_accounts_or_reach_unsafe_routes(deployed, sample_context):
    client, app, main = deployed
    auth, _ = provision(app)
    signed_in(client)
    drawing = drawing_for(client, auth, main, sample_context)
    client.cookies.clear()
    _, headers = signed_in(client, "trial02")
    for endpoint in ("dxf", "render", "download"):
        assert (
            client.get("/api/" + endpoint, params={"session_id": drawing.session_id}).status_code
            == 404
        )
    response = client.post(
        "/api/local-qa",
        headers=headers,
        json={
            "session_id": drawing.session_id,
            "question": "有什么图？",
        },
    )
    assert response.status_code == 404
    for endpoint in ("apply", "debug/traces", "admin/users", "auth/register", "auth/users"):
        assert client.post("/api/" + endpoint, headers=headers, json={}).status_code == 404
    assert client.post("/api/local-qa", json={}).status_code == 403
    assert client.get("/api/auth/session").json()["usage"]["daily_qa_remaining"] == 10


def test_qa_quota_blocks_before_model_and_survives_login_and_restart(
    deployed, sample_context, monkeypatch
):
    from web.backend.deepseek_qa import DeepSeekQA

    client, app, main = deployed
    auth, _ = provision(app, daily_limit=2, total_limit=3)
    _, headers = signed_in(client)
    drawing = drawing_for(client, auth, main, sample_context)
    calls = []

    async def answer(*_args):
        calls.append(1)
        return {"answer": "offline permissions fixture"}

    monkeypatch.setattr(DeepSeekQA, "answer", answer)
    body = {"session_id": drawing.session_id, "question": "有什么图？"}
    for _ in range(2):
        assert client.post("/api/local-qa", headers=headers, json=body).status_code == 200
    assert client.post("/api/local-qa", headers=headers, json=body).status_code == 429
    assert len(calls) == 2
    session, _ = signed_in(client)
    assert session["usage"]["daily_qa_remaining"] == 0
    restarted = AdminAuth(auth.config)
    _, user = restarted.login("trial01", PASSWORD, "fresh-peer")
    with pytest.raises(HTTPException) as blocked:
        restarted.reserve_trial_request(user, "qa")
    assert blocked.value.status_code == 429
    # Advancing the usage day refreshes daily quota, but never the lifetime cap.
    with auth._db() as db:
        db.execute("UPDATE trial_usage SET day = '2000-01-01'")
    lease = restarted.reserve_trial_request(user, "qa")
    restarted.finish_trial_request(lease)
    with pytest.raises(HTTPException) as total:
        restarted.reserve_trial_request(user, "qa")
    assert total.value.status_code == 429


def test_parallel_reservation_is_atomic_and_cross_login_lease_cannot_be_bypassed(deployed):
    _, app, _ = deployed
    auth, _ = provision(app)
    _, user = auth.login("trial01", PASSWORD, "test-peer")

    def reserve():
        try:
            return auth.reserve_trial_request(user, "qa")
        except HTTPException:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: reserve(), range(8)))
    leases = [result for result in results if result]
    assert len(leases) == 1
    _, new_user = auth.login("trial01", PASSWORD, "test-peer")
    with pytest.raises(HTTPException):
        auth.reserve_trial_request(new_user, "upload")
    auth.finish_trial_request(leases[0])
    lease = auth.reserve_trial_request(new_user, "upload")
    auth.finish_trial_request(lease)
    with auth._db() as db:
        assert tuple(db.execute("SELECT qa_count, upload_count FROM trial_usage").fetchone()) == (
            1,
            1,
        )


def test_upload_quota_and_only_latest_drawing_is_retained(deployed, sample_dxf, monkeypatch):
    client, app, main = deployed
    _, _ = provision(app, upload_daily_limit=2)
    monkeypatch.setattr(main, "RENDER_ENTITY_LIMIT", -1)
    _, headers = signed_in(client)
    drawings = []
    for _ in range(2):
        response = client.post(
            "/api/upload",
            headers=headers,
            files={"file": ("sample.dxf", sample_dxf.read_bytes(), "application/dxf")},
        )
        assert response.status_code == 200
        drawings.append(response.json()["session_id"])
    assert client.get("/api/dxf", params={"session_id": drawings[0]}).status_code == 404
    assert client.get("/api/dxf", params={"session_id": drawings[1]}).status_code == 200
    assert len(main.session_mgr._sessions) == 1
    assert (
        client.post(
            "/api/upload",
            headers=headers,
            files={"file": ("sample.dxf", sample_dxf.read_bytes(), "application/dxf")},
        ).status_code
        == 429
    )


def test_inflight_revocation_discards_answer_and_releases_job(
    deployed, sample_context, monkeypatch
):
    from web.backend.deepseek_qa import DeepSeekQA

    client, app, main = deployed
    auth, _ = provision(app)
    _, headers = signed_in(client)
    drawing = drawing_for(client, auth, main, sample_context)
    started = threading.Event()
    release = threading.Event()

    async def answer(*_args):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return {"answer": "must not be disclosed after revocation"}

    monkeypatch.setattr(DeepSeekQA, "answer", answer)
    body = {"session_id": drawing.session_id, "question": "有什么图？"}
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, "/api/local-qa", headers=headers, json=body)
        try:
            assert started.wait(3)
            denied = client.post("/api/local-qa", headers=headers, json=body)
            assert denied.status_code == 429
            update_account(auth, "trial01", enabled=0)
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    with sqlite3.connect(auth.config.db_path) as db:
        assert db.execute("SELECT count(*) FROM trial_jobs").fetchone()[0] == 0
        assert db.execute("SELECT SUM(qa_count) FROM trial_usage").fetchone()[0] == 1


def test_provider_failure_is_charged_and_does_not_leave_account_locked(
    deployed, sample_context, monkeypatch
):
    from web.backend.deepseek_qa import DeepSeekQA

    client, app, main = deployed
    auth, _ = provision(app)
    _, headers = signed_in(client)
    drawing = drawing_for(client, auth, main, sample_context)

    async def failed(*_args):
        raise HTTPException(502, "offline provider failure")

    monkeypatch.setattr(DeepSeekQA, "answer", failed)
    for _ in range(2):
        response = client.post(
            "/api/local-qa",
            headers=headers,
            json={"session_id": drawing.session_id, "question": "有什么图？"},
        )
        assert response.status_code == 502
    assert client.get("/api/auth/session").json()["usage"]["daily_qa_remaining"] == 8
