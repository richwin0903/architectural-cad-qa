"""Exercise the real production ASGI boundary, including original dependencies."""

import gzip
import importlib
import json
import math
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from web.backend.admin_auth import COOKIE_NAME, AuthConfig, hash_password, verify_password

PASSWORD = "a-long-test-password-123"  # noqa: S105 - non-production test fixture
ORIGIN = "https://wenjunjia.cloud"
PREFIX = "/cad"


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    encoded = hash_password(PASSWORD)
    monkeypatch.setenv("CAD_WEB_DEV_MODE", "false")
    monkeypatch.setenv("CAD_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("CAD_PUBLIC_PREFIX", PREFIX)
    monkeypatch.setenv("CAD_AUTH_DB", str(tmp_path / "import-auth.sqlite3"))
    monkeypatch.setenv("CAD_ADMIN_PASSWORD_HASH", encoded)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>CAD application</html>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('cad')")
    monkeypatch.setenv("CAD_FRONTEND_DIST", str(dist))
    production = importlib.import_module("web.backend.production")
    from web.backend import main
    from web.backend.session import SessionManager

    monkeypatch.setattr(main, "session_mgr", SessionManager())
    config = AuthConfig(ORIGIN, PREFIX, tmp_path / "auth.sqlite3", "admin", encoded)
    app = production.create_app(config, session_cleanup_dir=tmp_path / "uploads")
    with TestClient(app, base_url=ORIGIN + PREFIX, root_path=PREFIX) as client:
        yield client, app, main


def login(client, password=PASSWORD, username="admin"):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": ORIGIN},
    )


def test_hash_never_stores_plaintext():
    encoded = hash_password(PASSWORD)
    assert PASSWORD not in encoded
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong", encoded)
    assert hash_password(PASSWORD) != encoded


def test_protected_page_and_entire_api_surface(deployed):
    client, _, _ = deployed
    assert client.get("/login").status_code == 200
    page = client.get("/local-qa", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/cad/login"
    for path in ("local-status", "local-demo", "local-font", "dxf", "render", "download"):
        assert client.get("/api/" + path).status_code == 401
    for path in ("upload", "local-qa", "entities/near"):
        assert client.post("/api/" + path, json={}).status_code == 401
    assert client.get("/api/health").json() == {"status": "ok"}


def test_session_cookie_flags_and_csrf(deployed):
    client, _, _ = deployed
    assert (
        client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/cad", "Max-Age=28800"):
        assert flag in cookie
    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json() == response.json()
    assert client.get("/local-qa").status_code == 200
    csrf = response.json()["csrf_token"]
    assert client.post("/api/local-qa", json={}, headers={"Origin": ORIGIN}).status_code == 403
    assert client.post("/api/local-qa", json={}, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert (
        client.post(
            "/api/local-qa",
            json={},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/local-qa", json={}, headers={"X-CSRF-Token": csrf, "Origin": ORIGIN}
        ).status_code
        == 422
    )


def test_logout_revokes_replayed_cookie_and_removes_drawings(deployed):
    client, app, main = deployed
    response = login(client)
    cookie = client.cookies.get(COOKIE_NAME)
    uid = app.state.admin_auth.token_digest(cookie)
    drawing = main.session_mgr.create(uid)
    drawing.original_path.write_text("protected drawing")
    assert client.get("/api/dxf", params={"session_id": drawing.session_id}).status_code == 200
    logout = client.post(
        "/api/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]},
    )
    assert logout.status_code == 200
    assert not drawing.original_path.exists()
    client.cookies.set(COOKIE_NAME, cookie, path=PREFIX)
    assert client.get("/api/dxf", params={"session_id": drawing.session_id}).status_code == 401
    assert client.get("/api/auth/session").status_code == 401
    client.cookies.clear()
    assert login(client).status_code == 200
    assert client.get("/api/dxf", params={"session_id": drawing.session_id}).status_code == 404


def test_expiration_is_server_enforced_even_with_cookie(deployed):
    client, app, _ = deployed
    assert login(client).status_code == 200
    with sqlite3.connect(app.state.admin_auth.config.db_path) as db:
        db.execute("UPDATE admin_sessions SET expires_at = ?", (time.time() - 1,))
    assert client.get("/api/auth/session").status_code == 401
    assert client.get("/api/local-demo").status_code == 401


def test_failures_are_rate_limited_and_persisted(deployed):
    client, app, _ = deployed
    for _ in range(5):
        assert login(client, "incorrect-password").status_code == 401
    denied = login(client)
    assert denied.status_code == 429
    assert int(denied.headers["Retry-After"]) > 0
    with sqlite3.connect(app.state.admin_auth.config.db_path) as db:
        assert db.execute("SELECT count(*) FROM login_failures").fetchone()[0] == 5


def test_upstream_edit_debug_docs_endpoints_are_not_mounted(deployed):
    client, _, _ = deployed
    auth = login(client).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": auth["csrf_token"]}
    for path in (
        "apply",
        "plan",
        "v2/prompt",
        "revision/upload",
        "documents",
        "undo",
        "redo",
        "snapshot",
        "debug/traces",
        "v1/analyze",
    ):
        assert client.post("/api/" + path, json={}, headers=headers).status_code == 404
    for path in ("/openapi.json", "/docs", "/redoc", "/api/profile", "/api/workspace"):
        assert client.get(path).status_code == 404


def test_dev_mode_cannot_start_production(deployed, monkeypatch):
    _, app, _ = deployed
    from web.backend.production import create_app

    for value in ("1", "true", "yes", ""):
        monkeypatch.setenv("CAD_WEB_DEV_MODE", value)
        with pytest.raises(RuntimeError, match="CAD_WEB_DEV_MODE=false"):
            create_app(app.state.admin_auth.config)


def test_password_rotation_invalidates_sessions(deployed):
    client, app, _ = deployed
    assert login(client).status_code == 200
    from web.backend.admin_auth import AdminAuth

    previous = app.state.admin_auth.config
    rotated = AdminAuth(
        AuthConfig(
            ORIGIN, PREFIX, previous.db_path, "admin", hash_password("another-long-password-123")
        )
    )
    assert rotated.active_uids() == set()
    assert client.get("/api/auth/session").status_code == 401


def test_original_dependency_is_overridden_and_pdf_is_closed(deployed):
    client, _, _ = deployed
    auth = login(client).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": auth["csrf_token"]}
    # A real reused endpoint, with its original Depends(get_user), reaches
    # ownership validation instead of failing Firebase/license authentication.
    assert client.get("/api/dxf", params={"session_id": "absent"}).status_code == 404
    assert (
        client.post(
            "/api/upload",
            files={"file": ("drawing.pdf", b"pdf", "application/pdf")},
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/upload",
            files={"file": ("drawing.dwg", b"dwg", "application/octet-stream")},
            headers=headers,
        ).status_code
        == 422
    )


def test_auth_store_permissions(deployed):
    _, app, _ = deployed
    assert Path(app.state.admin_auth.config.db_path).stat().st_mode & 0o777 == 0o600


def test_cleanup_does_not_delete_a_concurrent_login_upload(deployed, monkeypatch):
    _, app, main = deployed
    created = []

    def active_uids_with_concurrent_upload():
        # Reproduce a new login/upload between cleanup's two snapshots.
        created.append(main.session_mgr.create("new-login"))
        return set()

    monkeypatch.setattr(app.state.admin_auth, "active_uids", active_uids_with_concurrent_upload)
    app.state.cleanup()
    assert main.session_mgr.get(created[0].session_id, "new-login") is created[0]
    main.session_mgr.delete(created[0].session_id)


def test_unicode_wrong_username_is_rejected_and_rate_limited(deployed):
    client, _, _ = deployed
    for _ in range(5):
        assert login(client, username="错误的管理员").status_code == 401
    assert login(client).status_code == 429


def test_unicode_password_hash_and_malformed_surrogates(deployed):
    client, app, _ = deployed
    unicode_password = "合法中文密码🔐包含十二个以上字符"  # noqa: S105 - test fixture
    encoded = hash_password(unicode_password)
    assert verify_password(unicode_password, encoded)
    assert not verify_password("\ud800", encoded)
    with pytest.raises(ValueError, match="valid Unicode"):
        hash_password("\ud800" * 12)
    for username, password in (("\ud800", PASSWORD), ("admin", "\ud800")):
        response = client.post(
            "/api/auth/login",
            content=json.dumps({"username": username, "password": password}),
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert "password" not in response.text and "username" not in response.text
        with pytest.raises(HTTPException) as invalid:
            app.state.admin_auth.login(username, password, "malformed-test-peer")
        assert invalid.value.status_code == 401
    with sqlite3.connect(app.state.admin_auth.config.db_path) as db:
        assert db.execute("SELECT count(*) FROM login_failures").fetchone()[0] == 2


def test_non_ascii_csrf_is_rejected_without_server_error(deployed):
    client, _, _ = deployed
    assert login(client).status_code == 200
    response = client.post(
        "/api/auth/logout",
        headers=[(b"Origin", ORIGIN.encode()), (b"X-CSRF-Token", "错误令牌".encode())],
    )
    assert response.status_code == 403
    assert client.get("/api/auth/session").status_code == 200


def test_dwg_upload_normalizes_metadata_and_keeps_preview_auth(
    deployed, tmp_path, monkeypatch, sample_dxf
):
    client, _, main = deployed
    executable = tmp_path / "dwg2dxf"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        f"pathlib.Path(sys.argv[4]).write_bytes(pathlib.Path({str(sample_dxf)!r}).read_bytes())\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("CAD_DWG_EXECUTABLE", str(executable))
    monkeypatch.setenv("CAD_DWG_CONVERTER", "libredwg")
    signed_in = login(client).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]}
    status = client.get("/api/local-status")
    assert status.json()["dwg_available"] is True
    assert status.json()["dwg_converter"] == "LibreDWG"
    response = client.post(
        "/api/upload",
        files={"file": ("建筑示例.dwg", b"AC1032fixture", "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    info = response.json()["file_info"]
    assert info["filename"] == "建筑示例.dwg"
    assert info["source_format"] == "dwg" and info["normalized_format"] == "dxf"
    assert "人工核对" in info["conversion_notice"]
    sid = response.json()["session_id"]
    drawing = main.session_mgr._sessions[sid]
    assert drawing.original_path.suffix == ".dxf"
    assert client.get("/api/dxf", params={"session_id": sid}).status_code == 200
    assert client.get("/api/render", params={"session_id": sid}).status_code == 200
    assert list(drawing.original_path.parent.glob("*.dwg")) == []
    client.post("/api/auth/logout", headers=headers)
    assert client.get("/api/dxf", params={"session_id": sid}).status_code == 401


def test_failed_normalized_dxf_removes_created_session(deployed, tmp_path, monkeypatch):
    client, _, main = deployed
    executable = tmp_path / "dwg2dxf"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib,sys\n"
        "pathlib.Path(sys.argv[4]).write_bytes(b'corrupt output')\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("CAD_DWG_EXECUTABLE", str(executable))
    signed_in = login(client).json()
    response = client.post(
        "/api/upload",
        files={"file": ("corrupt.dwg", b"AC1032fixture", "application/octet-stream")},
        headers={"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]},
    )
    assert response.status_code == 422 and "无法解析" in response.json()["detail"]
    assert not main.session_mgr._sessions


def test_upload_parsing_does_not_block_api_event_loop(deployed, monkeypatch):
    client, _, main = deployed
    started = threading.Event()
    release = threading.Event()

    def blocking_parser(file, user, session_manager, **_settings):
        started.set()
        assert release.wait(timeout=5)
        drawing = main.session_mgr.create(user["uid"])
        return {"session_id": drawing.session_id, "file_info": drawing.file_info}

    production = importlib.import_module("web.backend.production")
    monkeypatch.setattr(production, "parse_readonly_upload", blocking_parser)
    signed_in = login(client).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.post,
            "/api/upload",
            files={"file": ("drawing.dxf", b"test fixture", "application/dxf")},
            headers=headers,
        )
        try:
            assert started.wait(timeout=2)
            before = time.monotonic()
            assert client.get("/api/health").status_code == 200
            assert time.monotonic() - before < 1
        finally:
            release.set()
        assert future.result(timeout=3).status_code == 200
    client.post("/api/auth/logout", headers=headers)


def test_logout_during_worker_rejects_inflight_and_queued_uploads(deployed, monkeypatch):
    client, _, main = deployed
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_parser(file, user, session_manager, **_settings):
        calls.append(file.filename)
        started.set()
        assert release.wait(timeout=5)
        # Simulate a worker registering a drawing after logout removed previous
        # drawings; the boundary must clean it and return 401.
        drawing = main.session_mgr.create(user["uid"])
        return {"session_id": drawing.session_id, "file_info": drawing.file_info}

    production = importlib.import_module("web.backend.production")
    monkeypatch.setattr(production, "parse_readonly_upload", blocked_parser)
    signed_in = login(client).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            client.post,
            "/api/upload",
            files={"file": ("first.dxf", b"fixture", "application/dxf")},
            headers=headers,
        )
        assert started.wait(timeout=2)
        second = executor.submit(
            client.post,
            "/api/upload",
            files={"file": ("queued.dxf", b"fixture", "application/dxf")},
            headers=headers,
        )
        try:
            assert client.post("/api/auth/logout", headers=headers).status_code == 200
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 401
        assert second.result(timeout=3).status_code == 401
    assert calls == ["first.dxf"] and not main.session_mgr._sessions


def test_logout_during_conversion_prevents_parse(deployed, tmp_path, monkeypatch, sample_dxf):
    client, _, main = deployed
    started = tmp_path / "conversion-started"
    release = tmp_path / "release-converter"
    executable = tmp_path / "dwg2dxf"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        f"pathlib.Path({str(started)!r}).touch()\n"
        f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(0.02)\n"
        f"pathlib.Path(sys.argv[4]).write_bytes(pathlib.Path({str(sample_dxf)!r}).read_bytes())\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("CAD_DWG_EXECUTABLE", str(executable))
    calls = []

    def should_not_parse(file, user, session_manager, **_settings):
        calls.append(file.filename)
        raise AssertionError("Revoked conversion must never reach the parser")

    production = importlib.import_module("web.backend.production")
    monkeypatch.setattr(production, "parse_readonly_upload", should_not_parse)
    signed_in = login(client).json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.post,
            "/api/upload",
            files={"file": ("drawing.dwg", b"AC1032fixture", "application/octet-stream")},
            headers=headers,
        )
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        try:
            assert started.exists()
            assert client.post("/api/auth/logout", headers=headers).status_code == 200
        finally:
            release.touch()
        assert future.result(timeout=3).status_code == 401
    assert not calls and not main.session_mgr._sessions


def test_real_libredwg_dxf_reads_and_renders_without_editor_reserialization(deployed, monkeypatch):
    import ezdxf.document

    client, _, main = deployed
    source = gzip.decompress(
        (Path(__file__).parent / "fixtures/libredwg-line-2018.dxf.gz").read_bytes()
    )

    def forbidden_write(*_args, **_kwargs):
        raise AssertionError("Read-only QA must never rewrite converter-produced DXF metadata")

    monkeypatch.setattr(ezdxf.document.Drawing, "write", forbidden_write)
    signed_in = login(client).json()
    response = client.post(
        "/api/upload",
        files={"file": ("real-libredwg-2018-line.dxf", source, "application/dxf")},
        headers={"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["file_info"]["dxf_encoding"] == "utf-8"
    assert response.json()["file_info"]["dxf_version"] == "AC1032"
    sid = response.json()["session_id"]
    drawing = main.session_mgr._sessions[sid]
    assert drawing.working_path is None and drawing.edit_history is None
    assert drawing.original_path.read_bytes() == source
    context = drawing.context
    assert context.entity_count == 1
    doc = ezdxf.readfile(drawing.original_path)
    line = next(iter(doc.modelspace().query("LINE")))
    assert math.isclose((line.dxf.end - line.dxf.start).magnitude, 17.4075, abs_tol=0.002)
    preview = client.get("/api/render", params={"session_id": sid})
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
    assert client.get("/api/dxf", params={"session_id": sid}).content == source


@pytest.mark.parametrize(
    ("python_encoding", "browser_encoding"),
    [
        ("utf8", "utf-8"),
        ("gbk", "gbk"),
        ("cp936", "gbk"),
        ("cp950", "big5"),
        ("cp932", "shift_jis"),
        ("cp949", "euc-kr"),
        ("cp1252", "windows-1252"),
    ],
)
def test_dxf_codepage_metadata_uses_browser_decoder_aliases(
    deployed, python_encoding, browser_encoding
):
    production = importlib.import_module("web.backend.production")
    assert production.browser_dxf_encoding(python_encoding) == browser_encoding


def test_legacy_gbk_dxf_preserves_chinese_and_reports_codepage(deployed, tmp_path, monkeypatch):
    import ezdxf

    client, _, main = deployed
    doc = ezdxf.new("R2000")
    doc.encoding = "gbk"
    doc.header["$DWGCODEPAGE"] = "ANSI_936"
    doc.modelspace().add_text("会议室", dxfattribs={"height": 280, "insert": (1550, 4000)})
    path = tmp_path / "legacy-gbk.dxf"
    doc.saveas(path)
    source = path.read_bytes()
    monkeypatch.setattr(main, "RENDER_ENTITY_LIMIT", -1)
    signed_in = login(client).json()
    response = client.post(
        "/api/upload",
        files={"file": (path.name, source, "application/dxf")},
        headers={"Origin": ORIGIN, "X-CSRF-Token": signed_in["csrf_token"]},
    )
    assert response.status_code == 200
    info = response.json()["file_info"]
    assert info["dxf_encoding"] == "gbk" and info["dxf_version"] == "AC1015"
    drawing = main.session_mgr._sessions[response.json()["session_id"]]
    assert drawing.original_path.read_bytes() == source
    assert next(iter(ezdxf.readfile(drawing.original_path).modelspace())).dxf.text == "会议室"
