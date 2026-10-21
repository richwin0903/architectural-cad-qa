"""Publication checks inspect release bytes without exposing matched secrets."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-release.py"
SPEC = importlib.util.spec_from_file_location("release_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def token() -> bytes:
    return b"sk-" + hashlib.sha256(b"release-check-regression-fixture").hexdigest()[:32].encode()


class ReleaseCheckTests(unittest.TestCase):
    def test_high_confidence_provider_and_github_tokens(self):
        github = b"ghp_" + hashlib.sha256(b"github-regression-fixture").hexdigest()[:36].encode()
        for value in (token(), github):
            with self.subTest(prefix=value[:3]):
                self.assertIn("provider-token", CHECK.inspect("tests/example.py", value))

    def test_templates_are_not_exempt_from_real_token_detection(self):
        self.assertIn("provider-token", CHECK.inspect(".env.example", token()))

    def test_empty_and_explicit_placeholders_are_allowed(self):
        examples = (b"", b"DEEPSEEK_API_KEY=", b"DEEPSEEK_API_KEY=your-api-key",
                    b"DEEPSEEK_API_KEY=local-test-placeholder", b"sk-test-" + b"a" * 48)
        for value in examples:
            self.assertFalse(CHECK.inspect(".env.example", value))
        self.assertFalse(CHECK.inspect(".env.production.example", b"DEEPSEEK_API_KEY="))

    def test_repeated_dummy_token_is_not_a_live_key(self):
        self.assertFalse(CHECK.inspect("tests/example.py", b"sk-" + b"0" * 32))

    def test_private_key_header_is_detected_without_echoing_material(self):
        header = b"-----BEGIN " + b"PRIVATE KEY-----"
        self.assertIn("private-key", CHECK.inspect("config.txt", header))

    def test_runtime_and_dependency_paths_are_denied(self):
        names = (".env", ".env.production", ".secrets/runtime.env", "state.sqlite3",
                 "server.log", ".evidence/report.json", "node_modules/pkg/index.js",
                 "web/frontend/dist/index.html", ".venv/bin/python", "id_ed25519")
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(CHECK.inspect(name, b""))

    def test_frontend_build_plugin_is_source_and_allowed(self):
        self.assertFalse(CHECK.inspect("web/frontend/build/dxfFontAliases.js", b""))

    def test_sqlite_magic_is_denied_even_with_an_innocent_name(self):
        self.assertIn("database-content", CHECK.inspect("data.bin", b"SQLite format 3\0"))

    def test_reviewed_artifact_requires_its_exact_checksum(self):
        name = next(iter(CHECK.REVIEWED))
        self.assertIn("reviewed-artifact-checksum-mismatch", CHECK.inspect(name, b"changed"))

    def test_tree_honors_gitignore_in_a_copy_without_dot_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitignore").write_text(".env\nnode_modules/\n")
            (root / ".env").write_bytes(token())
            (root / "README.md").write_text("clean")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "private.js").write_bytes(token())
            with patch.object(CHECK, "ROOT", root):
                names = {name for name, _, _ in CHECK.snapshot(False)}
            self.assertEqual(names, {".gitignore", "README.md"})

    def test_index_reads_staged_bytes_instead_of_changed_working_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", directory], check=True)
            file = root / "example.txt"
            file.write_text("clean")
            subprocess.run(["git", "-C", directory, "add", "example.txt"], check=True)
            file.write_bytes(token())
            with patch.object(CHECK, "ROOT", root):
                staged = list(CHECK.snapshot(True))
            self.assertEqual(staged, [("example.txt", b"clean", None)])

    def test_file_at_95_mib_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "too-large.bin").open("wb") as handle:
                handle.truncate(CHECK.MAX_BYTES)
            with patch.object(CHECK, "ROOT", root):
                self.assertEqual(list(CHECK.snapshot(False)),
                                 [("too-large.bin", None, "oversize-file")])

    def test_zip_enclosing_directory_is_normalized_without_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("project-main/README.md", "clean")
                handle.writestr("project-main/.env", "private")
            files = list(CHECK.archive_snapshot(archive))
            self.assertEqual(files[0], ("README.md", b"clean", None))
            self.assertIn("environment-file", CHECK.inspect(files[1][0], files[1][1]))
            self.assertFalse((Path(directory) / "project-main").exists())

    def test_tar_path_traversal_is_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.tar"
            with tarfile.open(archive, "w") as handle:
                member = tarfile.TarInfo("../outside.txt")
                member.size = 3
                handle.addfile(member, io.BytesIO(b"bad"))
            name, data, error = next(CHECK.archive_snapshot(archive))
            self.assertIsNone(error)
            self.assertIn("unsafe-path", CHECK.inspect(name, data))
            self.assertEqual(CHECK.archive_prefix(["../README.md", "../config.py"]), "")

    def test_zip_symlink_is_rejected_without_dereferencing(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                member = zipfile.ZipInfo("linked.txt")
                member.create_system = 3
                member.external_attr = 0o120777 << 16
                handle.writestr(member, "/outside/private")
            self.assertEqual(next(CHECK.archive_snapshot(archive)), ("linked.txt", None, "symlink"))

    def test_cli_never_prints_the_matched_secret_even_in_a_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            value = token().decode()
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr(value + ".txt", token())
            output = io.StringIO()
            with patch.object(sys, "argv", [str(SCRIPT), "--archive", str(archive)]), \
                    contextlib.redirect_stdout(output):
                result = CHECK.main()
            self.assertEqual(result, 1)
            self.assertNotIn(value, output.getvalue())
            self.assertIn("provider-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
