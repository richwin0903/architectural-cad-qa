"""Exercise publication guards without storing real credentials or private files."""

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location(
        "release_check", ROOT / "scripts/check-release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_detects_private_paths_tokens_and_unreviewed_artifacts(checker):
    assert "environment-file" in checker.inspect(".env.production", b"dummy")
    assert "private-runtime-path" in checker.inspect(".secrets/runtime.env", b"dummy")
    assert "secret-log-or-database-file" in checker.inspect("debug.log", b"dummy")
    assert "database-content" in checker.inspect("data.bin", b"SQLite format 3\x00")
    assert "unreviewed-archive" in checker.inspect("upload.zip", b"dummy")
    assert "unreviewed-drawing" in checker.inspect("client.dwg", b"dummy")
    token = b"sk-" + hashlib.sha384(b"publication-test-token").hexdigest()[:48].encode()
    assert "provider-token" in checker.inspect("config.py", token)
    key = b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----"
    assert "private-key" in checker.inspect("data.txt", key)


def test_allows_placeholders_but_checks_reviewed_artifact_bytes(checker):
    assert not checker.inspect(".env.example", b"DEEPSEEK_API_KEY=your-api-key")
    assert not checker.inspect("tests/example.py", b'DEEPSEEK_API_KEY="local-test-placeholder"')
    fixture = "local_examples/architectural-demo.dxf"
    data = (ROOT / fixture).read_bytes()
    assert not checker.inspect(fixture, data)
    assert "reviewed-artifact-checksum-mismatch" in checker.inspect(fixture, data + b"change")


def test_index_checks_staged_bytes_not_working_copy(checker, monkeypatch, tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "config.py"
    token = b"sk-" + hashlib.sha384(b"staged-publication-test-token").hexdigest()[:48].encode()
    path.write_bytes(token)
    subprocess.run(["git", "-C", str(tmp_path), "add", "config.py"], check=True)
    path.write_text("safe working copy")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    entries = list(checker.snapshot(index=True))
    assert entries == [("config.py", token, None)]
    assert "provider-token" in checker.inspect(entries[0][0], entries[0][1])
    monkeypatch.setattr(checker, "REQUIRED", {"config.py"})
    monkeypatch.setattr(checker.sys, "argv", ["check-release.py", "--index"])
    assert checker.main() == 1
    output = capsys.readouterr().out
    assert "config.py" in output
    assert "provider-token" in output
    assert token.decode() not in output


def test_tree_rejects_symlinks_without_reading_their_target(checker, monkeypatch, tmp_path):
    (tmp_path / "outside").symlink_to(tmp_path.parent / "not-readable")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    assert list(checker.snapshot(index=False)) == [("outside", None, "symlink")]
