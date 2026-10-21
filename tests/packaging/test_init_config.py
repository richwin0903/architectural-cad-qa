"""Check protected configuration creation without a terminal, network, or application imports."""

import hashlib
import importlib.util
import io
import warnings
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/init-config.py"
SPEC = importlib.util.spec_from_file_location("cad_init_config", SCRIPT)
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


def test_creates_protected_raw_config_and_compatible_scrypt_hash(tmp_path, capsys):
    password = "管理员密码包含十二个以上字符"  # noqa: S105 - synthetic test credential
    key = "test-key-with-$-and-#-raw-format"
    target = config.create_config(tmp_path, "admin", password, key)
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    values = dict(line.split("=", 1) for line in target.read_text().splitlines())
    algorithm, n, r, p, salt, digest = values["CAD_ADMIN_PASSWORD_HASH"].split("$")
    assert algorithm == "scrypt" and (int(n), int(r), int(p)) == (16384, 8, 1)
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
    )
    assert actual.hex() == digest and password not in target.read_text()
    assert values["DEEPSEEK_API_KEY"] == key
    assert not capsys.readouterr().out


def test_never_overwrites_existing_config(tmp_path):
    target = config.create_config(tmp_path, "admin", "a-long-test-password", "")
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        config.create_config(tmp_path, "other-admin", "another-long-password", "different-key")
    assert target.read_bytes() == original


@pytest.mark.parametrize("field", ["username", "password", "api_key"])
@pytest.mark.parametrize("forbidden", ["\n", "\r", "\0", "\ud800"])
def test_rejects_invalid_values_before_creating_files(tmp_path, field, forbidden):
    arguments = {"username": "admin", "password": "a-long-test-password", "api_key": "test-key"}
    arguments[field] += forbidden
    with pytest.raises(ValueError):
        config.create_config(tmp_path, **arguments)
    assert not (tmp_path / ".secrets/runtime.env").exists()


def test_rejects_weak_password_and_symlink_directory(tmp_path):
    with pytest.raises(ValueError, match="12 characters"):
        config.create_config(tmp_path, "admin", "short", "")
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / ".secrets").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        config.create_config(tmp_path, "admin", "a-long-test-password", "")
    assert not (other / "runtime.env").exists()


def test_hidden_prompts_allow_skip_key_and_do_not_echo_secrets(tmp_path, monkeypatch, capsys):
    password = "test-password-kept-private"  # noqa: S105 - synthetic test credential
    inputs = iter([password, password, ""])
    monkeypatch.setattr(config.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(config.getpass, "getpass", lambda _prompt: next(inputs))
    assert config.main(["--directory", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert "unconfigured" in output.out and password not in output.out + output.err
    assert "DEEPSEEK_API_KEY=\n" in (tmp_path / ".secrets/runtime.env").read_text()
    assert config.main(["--directory", str(tmp_path)]) == 0
    assert "not changed" in capsys.readouterr().out


def test_mismatched_confirmation_does_not_create_config(tmp_path, monkeypatch, capsys):
    inputs = iter(["a-long-test-password", "different-password"])
    monkeypatch.setattr(config.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(config.getpass, "getpass", lambda _prompt: next(inputs))
    assert config.main(["--directory", str(tmp_path)]) == 1
    assert not (tmp_path / ".secrets/runtime.env").exists()
    assert "a-long-test-password" not in capsys.readouterr().err


def test_noninteractive_terminal_is_rejected_instead_of_echoing_password(tmp_path, monkeypatch):
    monkeypatch.setattr(config.sys, "stdin", io.StringIO())
    with pytest.raises(SystemExit) as error:
        config.main(["--directory", str(tmp_path)])
    assert error.value.code == 2
    assert not (tmp_path / ".secrets/runtime.env").exists()


def test_password_cannot_exceed_login_api_limit(tmp_path):
    with pytest.raises(ValueError, match="at most 1024"):
        config.create_config(tmp_path, "admin", "p" * 1025, "")
    assert not (tmp_path / ".secrets/runtime.env").exists()


def test_hidden_input_failure_never_falls_back_to_echo(tmp_path, monkeypatch, capsys):
    def unavailable(_prompt):
        warnings.warn(
            "Cannot control echo on the terminal.", config.getpass.GetPassWarning, stacklevel=2
        )
        pytest.fail("Input must not be requested after hidden terminal input fails")

    monkeypatch.setattr(config.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(config.getpass, "getpass", unavailable)
    assert config.main(["--directory", str(tmp_path)]) == 1
    assert "Hidden input is unavailable" in capsys.readouterr().err
    assert not (tmp_path / ".secrets/runtime.env").exists()


def test_public_template_is_copied_once_without_overwriting(tmp_path, monkeypatch, capsys):
    template = "CAD_PUBLIC_ORIGIN=https://localhost:8443\nCAD_HTTPS_PORT=8443\n"
    (tmp_path / ".env.example").write_text(template)
    inputs = iter(["a-long-test-password", "a-long-test-password", ""])
    monkeypatch.setattr(config.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(config.getpass, "getpass", lambda _prompt: next(inputs))
    assert config.main(["--directory", str(tmp_path)]) == 0
    assert (tmp_path / ".env").read_text() == template
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    (tmp_path / ".env").write_text("CAD_HTTPS_PORT=19443\n")
    assert config.main(["--directory", str(tmp_path)]) == 0
    assert (tmp_path / ".env").read_text() == "CAD_HTTPS_PORT=19443\n"
    assert "preserved" in capsys.readouterr().out


def test_public_config_rejects_secret_fields_and_invalid_username(tmp_path):
    (tmp_path / ".env.example").write_text("DEEPSEEK_API_KEY=placeholder\n")
    with pytest.raises(ValueError, match="only non-secret"):
        config.copy_public_config(tmp_path)
    assert not (tmp_path / ".env").exists()
    with pytest.raises(ValueError, match="equals sign"):
        config.create_config(tmp_path, "admin=not-an-env-key", "a-long-test-password", "")
    assert not (tmp_path / ".secrets/runtime.env").exists()
