"""Spawn / stop an ``aigw run`` subprocess.

Used by :meth:`envoyai.Gateway.local` (background) and
:meth:`envoyai.Gateway.serve` (foreground). The renderer produces a
multi-doc YAML; this module writes it to a temp file, starts ``aigw``,
waits for readiness, and exposes a clean ``stop()`` path.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from envoyai.errors import LocalRunError

__all__ = [
    "find_aigw",
    "write_config",
    "spawn_background",
    "run_foreground",
    "probe_ready",
]


def find_aigw(*, auto_download: bool = True, verbose: bool = True) -> Path:
    """Locate the ``aigw`` binary.

    Resolution order is ``$ENVOYAI_AIGW_PATH`` → ``$PATH`` → envoyai cache
    → download-and-cache. See
    :func:`envoyai._internal.aigw_bootstrap.resolve_binary` for the full
    semantics.
    """
    from envoyai._internal.aigw_bootstrap import resolve_binary

    return resolve_binary(auto_download=auto_download, verbose=verbose)


def write_config(yaml_text: str, *, prefix: str = "envoyai-") -> Path:
    """Write the rendered YAML to a temp file and return the path."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(yaml_text)
    return Path(path)


def spawn_background(
    config_path: Path,
    *,
    admin_port: int,
    debug: bool,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start ``aigw run`` as a background subprocess."""
    aigw = find_aigw()
    cmd: list[str] = [str(aigw), "run", str(config_path), "--admin-port", str(admin_port)]
    if debug:
        cmd.append("--debug")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )


def run_foreground(
    config_path: Path,
    *,
    admin_port: int,
    debug: bool,
    env: dict[str, str] | None = None,
) -> int:
    """Run ``aigw run`` in the foreground, inheriting stdio. Blocks until the
    child exits or receives a signal. Returns the child's exit code."""
    aigw = find_aigw()
    cmd: list[str] = [str(aigw), "run", str(config_path), "--admin-port", str(admin_port)]
    if debug:
        cmd.append("--debug")
    proc = subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        stdin=sys.stdin,
        env={**os.environ, **(env or {})},
    )
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        return proc.wait()


def probe_ready(port: int, *, timeout_s: float = 20.0) -> None:
    """Poll ``http://127.0.0.1:<port>/v1/models`` until it answers or the
    timeout fires. Any 2xx/4xx response counts as ready (we just need the
    listener to be accepting HTTP); 5xx and connection errors do not."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    url = f"http://127.0.0.1:{port}/v1/models"
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code < 500:
                return
            last_err = RuntimeError(f"HTTP {r.status_code} from {url}")
        except httpx.RequestError as e:
            last_err = e
        time.sleep(0.2)
    raise LocalRunError(
        f"aigw did not become ready on port {port} within {timeout_s:.0f}s"
        + (f" (last error: {last_err})" if last_err else "")
    )


def stop_background(proc: subprocess.Popen[bytes], *, grace_s: float = 5.0) -> None:
    """SIGTERM, wait up to ``grace_s``, then SIGKILL if still alive."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
