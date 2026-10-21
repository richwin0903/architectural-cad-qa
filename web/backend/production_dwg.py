"""Bounded, local-only DWG normalization for the production upload endpoint.

The GNU LibreDWG CLI runs in a private temporary directory with no API secrets
in its environment. It is never invoked through a shell. Temporary inputs and
outputs are removed after parsing succeeds, fails, times out, or is cancelled.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import stat
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

MAX_DWG_BYTES = 25 * 1024 * 1024
MAX_DXF_BYTES = 25 * 1024 * 1024
CONVERSION_TIMEOUT_SECONDS = 60


def dwg_executable() -> Path | None:
    """Use the exact same capability check for status and upload."""
    if os.getenv("CAD_DWG_CONVERTER", "libredwg").strip().lower() != "libredwg":
        return None
    configured = os.getenv("CAD_DWG_EXECUTABLE", "").strip()
    candidate = configured or shutil.which("dwg2dxf")
    if not candidate:
        return None
    path = Path(candidate)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        return None
    return path


def _check_output_tree(directory: Path) -> None:
    """Bound both the normalized DXF and aggregate temporary converter output."""
    total = 0
    entries = 0
    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in [*dirs, *files]:
            entries += 1
            path = Path(root) / name
            mode = path.lstat().st_mode
            if entries > 32 or stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise HTTPException(422, "DWG 转换输出异常，请检查图纸或另存为 DXF。")
            if stat.S_ISREG(mode):
                total += path.stat().st_size
                if total > MAX_DXF_BYTES:
                    raise HTTPException(422, "转换后的 DXF 超过 25 MB，请精简图纸后重试。")


async def _terminate(process: asyncio.subprocess.Process) -> None:
    # Kill the entire converter process group, including any child processes.
    if process.returncode is not None:
        await process.wait()
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def _run_converter(executable: Path, source: Path, output: Path) -> None:
    # LibreDWG needs no application credentials. Do not inherit DeepSeek keys,
    # administrator hashes, session configuration, or unrelated environment.
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(source.parent.parent),
        "TMPDIR": str(output.parent),
    }
    try:
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "-v0",
            "-y",
            "-o",
            str(output),
            str(source),
            cwd=output.parent,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise HTTPException(503, "服务器 DWG 转换器暂时无法启动，请稍后重试。") from None
    try:
        async with asyncio.timeout(CONVERSION_TIMEOUT_SECONDS):
            while process.returncode is None:
                _check_output_tree(output.parent)
                try:
                    await asyncio.wait_for(process.wait(), timeout=0.1)
                except TimeoutError:
                    continue
            _check_output_tree(output.parent)
            if process.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
                raise HTTPException(
                    422, "无法转换该 DWG，文件可能损坏或版本不受支持；请另存为 DXF。"
                )
    except TimeoutError:
        await _terminate(process)
        raise HTTPException(504, "DWG 转换超过 60 秒，请精简图纸或另存为 DXF 后重试。") from None
    except BaseException:
        await _terminate(process)
        raise


@asynccontextmanager
async def normalized_dwg(data: bytes):
    """Yield a bounded normalized DXF path, valid only inside this context."""
    if len(data) > MAX_DWG_BYTES:
        raise HTTPException(413, "图纸最大为 25 MB。")
    if not re.fullmatch(rb"AC10[0-9]{2}", data[:6]):
        raise HTTPException(422, "文件不是有效的 DWG 图纸，请检查文件或另存为 DXF。")
    executable = dwg_executable()
    if executable is None:
        raise HTTPException(422, "服务器未配置可用的 DWG 转换器，请先将 DWG 转为 DXF。")
    temporary = tempfile.TemporaryDirectory(prefix="cad-dwg-")
    try:
        directory = Path(temporary.name)
        input_directory = directory / "input"
        output_directory = directory / "output"
        input_directory.mkdir(mode=0o700)
        output_directory.mkdir(mode=0o700)
        source = input_directory / "drawing.dwg"
        output = output_directory / "drawing.dxf"
        await run_in_threadpool(source.write_bytes, data)
        await _run_converter(executable, source, output)
        yield output
    finally:
        await run_in_threadpool(temporary.cleanup)
