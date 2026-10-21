"""Exercise real converter subprocess boundaries with small controlled CLIs."""

import asyncio
import os
import sys
import textwrap

import pytest
from fastapi import HTTPException
from web.backend import production_dwg as dwg


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD_DWG_CONVERTER", "libredwg")
    monkeypatch.setattr(dwg.tempfile, "tempdir", str(tmp_path))

    def install(code):
        executable = tmp_path / "dwg2dxf"
        executable.write_text(
            "#!" + sys.executable + "\n" + textwrap.dedent(code), encoding="utf-8"
        )
        executable.chmod(0o700)
        monkeypatch.setenv("CAD_DWG_EXECUTABLE", str(executable))
        return executable

    return install


def normalize(data=b"AC1032binary-fixture"):
    async def run():
        async with dwg.normalized_dwg(data) as path:
            return path, path.read_bytes()

    return asyncio.run(run())


def test_converter_argv_isolated_environment_and_cleanup(fake_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-placeholder")
    fake_cli("""
        import os, pathlib, sys
        assert len(sys.argv) == 6
        assert sys.argv[1:4] == ['-v0', '-y', '-o']
        assert 'DEEPSEEK_API_KEY' not in os.environ
        output, source = pathlib.Path(sys.argv[4]), pathlib.Path(sys.argv[5])
        assert source.name == 'drawing.dwg' and source.parent.name == 'input'
        assert output.name == 'drawing.dxf' and output.parent.name == 'output'
        assert source.parent.parent == output.parent.parent
        assert source.read_bytes().startswith(b'AC1032')
        output.write_bytes(b'normalized-dxf')
    """)
    path, content = normalize()
    assert content == b"normalized-dxf" and not path.exists()
    assert list(tmp_path.glob("cad-dwg-*")) == []


def test_corrupt_dwg_is_chinese_422_and_cleans_up(fake_cli, tmp_path):
    fake_cli("import sys; sys.exit(1)")
    with pytest.raises(HTTPException) as error:
        normalize()
    assert error.value.status_code == 422 and "损坏" in error.value.detail
    assert list(tmp_path.glob("cad-dwg-*")) == []
    with pytest.raises(HTTPException, match="有效"):
        normalize(b"not-a-DWG")


def test_input_limit_checked_before_subprocess(fake_cli, monkeypatch):
    fake_cli("raise RuntimeError('must not be invoked')")
    monkeypatch.setattr(dwg, "MAX_DWG_BYTES", 8)
    with pytest.raises(HTTPException) as error:
        normalize(b"AC1032long")
    assert error.value.status_code == 413


def test_output_limit_and_symlink_are_rejected(fake_cli, monkeypatch, tmp_path):
    monkeypatch.setattr(dwg, "MAX_DXF_BYTES", 1024)
    fake_cli("import pathlib,sys; pathlib.Path(sys.argv[4]).write_bytes(b'x' * 1025)")
    with pytest.raises(HTTPException) as error:
        normalize()
    assert error.value.status_code == 422 and "超过" in error.value.detail
    assert list(tmp_path.glob("cad-dwg-*")) == []
    fake_cli("import pathlib,sys; pathlib.Path(sys.argv[4]).symlink_to(sys.argv[5])")
    with pytest.raises(HTTPException) as error:
        normalize()
    assert error.value.status_code == 422 and "异常" in error.value.detail
    assert list(tmp_path.glob("cad-dwg-*")) == []


def test_timeout_kills_converter_and_children(fake_cli, monkeypatch, tmp_path):
    marker = tmp_path / "child-survived"
    child = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").touch()"
    fake_cli(f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, '-c', {child!r}])
        time.sleep(30)
    """)
    monkeypatch.setattr(dwg, "CONVERSION_TIMEOUT_SECONDS", 0.15)

    async def run():
        with pytest.raises(HTTPException) as error:
            async with dwg.normalized_dwg(b"AC1032fixture"):
                pytest.fail("timed-out converter yielded output")
        assert error.value.status_code == 504
        await asyncio.sleep(1.1)

    asyncio.run(run())
    assert not marker.exists()
    assert list(tmp_path.glob("cad-dwg-*")) == []


def test_cancellation_kills_converter_and_cleans_tempfiles(fake_cli, tmp_path):
    fake_cli("import time; time.sleep(30)")

    async def run():
        async def convert():
            async with dwg.normalized_dwg(b"AC1032fixture"):
                pytest.fail("cancelled converter yielded output")

        task = asyncio.create_task(convert())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert list(tmp_path.glob("cad-dwg-*")) == []


def test_capability_uses_the_configured_executable(fake_cli, monkeypatch):
    executable = fake_cli("pass")
    assert dwg.dwg_executable() == executable
    executable.chmod(0o600)
    assert dwg.dwg_executable() is None
    executable.chmod(0o700)
    monkeypatch.setenv("CAD_DWG_CONVERTER", "unsupported-converter")
    assert dwg.dwg_executable() is None
    assert os.access(executable, os.X_OK)
