"""Resolve the ``aigw`` binary: ``$ENVOYAI_AIGW_PATH`` → ``$PATH`` → cache →
download from GitHub releases.

The goal is that ``pip install envoyai`` is the only install step a user
needs. On the first ``gw.local()`` or ``gw.serve()`` call, if ``aigw`` isn't
already reachable, we fetch the pinned release asset into
``~/.cache/envoyai/bin/`` and run from there. Subsequent calls reuse the
cache.

Escape hatches:

* ``$ENVOYAI_AIGW_PATH`` — point at a specific binary, bypass the search.
* ``$PATH`` — any user-managed install (`brew`, `go install`) wins over
  the cache.
* ``envoyai download-aigw`` — pre-fetch (CI, air-gapped, Dockerfile RUN).
"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
from pathlib import Path

import httpx

from envoyai.errors import LocalRunError

__all__ = [
    "AIGW_VERSION",
    "cached_path",
    "ensure_downloaded",
    "resolve_binary",
]


#: Pinned aigw release this version of envoyai is built against. Update in
#: lockstep with the runtime renderer so the YAML schema and binary stay in
#: sync.
AIGW_VERSION = "0.5.0"


def resolve_binary(*, auto_download: bool = True, verbose: bool = True) -> Path:
    """Return a usable ``aigw`` path, downloading if necessary.

    Resolution order:

    1. ``$ENVOYAI_AIGW_PATH`` — must point at an existing file.
    2. ``$PATH`` — via :func:`shutil.which`.
    3. ``~/.cache/envoyai/bin/aigw-<AIGW_VERSION>`` — previously downloaded.
    4. Download into (3) and return that, if ``auto_download`` is True.

    Raises :class:`LocalRunError` if the binary is missing and
    ``auto_download`` is disabled, or the platform is unsupported.
    """
    override = os.environ.get("ENVOYAI_AIGW_PATH")
    if override:
        p = Path(override).expanduser()
        if not p.is_file():
            raise LocalRunError(
                f"ENVOYAI_AIGW_PATH={override!r} does not point at an "
                "existing file"
            )
        return p

    on_path = shutil.which("aigw")
    if on_path:
        return Path(on_path)

    cached = cached_path()
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached

    if not auto_download:
        raise LocalRunError(
            "aigw was not found on PATH or in the envoyai cache. Run "
            "`envoyai download-aigw` to fetch it, or set "
            "ENVOYAI_AIGW_PATH to an existing binary."
        )

    return ensure_downloaded(verbose=verbose)


def cached_path(version: str = AIGW_VERSION) -> Path:
    """Path where the cached binary would live (may not exist yet)."""
    return _cache_dir() / f"aigw-{version}"


def ensure_downloaded(
    *,
    version: str = AIGW_VERSION,
    verbose: bool = True,
) -> Path:
    """Download and cache the ``aigw`` binary. Returns the cached path.

    Idempotent — if the cached file already exists and is executable, this
    returns immediately without any network I/O.
    """
    dst = cached_path(version)
    if dst.is_file() and os.access(dst, os.X_OK):
        return dst

    os_tag, arch = _platform_tuple()
    url = _release_url(version, os_tag, arch)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")

    if verbose:
        print(
            f"envoyai: downloading aigw {version} ({os_tag}/{arch}) from\n"
            f"  {url}",
            file=sys.stderr,
        )

    try:
        _download(url, tmp)
    except httpx.HTTPStatusError as e:
        tmp.unlink(missing_ok=True)
        if e.response.status_code == 404:
            raise LocalRunError(
                f"aigw release asset {os_tag}/{arch} not found at {url}. "
                "Your platform may not have an official release. Build "
                "from source at https://github.com/envoyproxy/ai-gateway "
                "and set ENVOYAI_AIGW_PATH."
            ) from e
        raise LocalRunError(f"aigw download failed: {e}") from e
    except httpx.RequestError as e:
        tmp.unlink(missing_ok=True)
        raise LocalRunError(
            f"aigw download failed ({type(e).__name__}: {e}). If you are "
            "behind a corporate proxy, configure HTTPS_PROXY and retry."
        ) from e

    # Atomic move — the file either appears fully executable or not at all.
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    tmp.replace(dst)

    if verbose:
        print(f"envoyai: cached at {dst}", file=sys.stderr)
    return dst


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _cache_dir() -> Path:
    """Resolve the cache root. Honors ``$XDG_CACHE_HOME`` on all platforms for
    predictability in containers."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "envoyai" / "bin"


def _platform_tuple() -> tuple[str, str]:
    """Return (goos, goarch) matching aigw's release asset naming.

    aigw publishes: ``aigw-linux-amd64``, ``aigw-linux-arm64``,
    ``aigw-darwin-arm64``. Any platform outside that set triggers a clear
    error rather than a silent 404.
    """
    sysname = platform.system()
    machine = platform.machine().lower()

    if sysname == "Linux":
        goos = "linux"
    elif sysname == "Darwin":
        goos = "darwin"
    else:
        raise LocalRunError(
            f"envoyai auto-download does not support {sysname!r} — only "
            "Linux and macOS (arm64) have official aigw releases. Set "
            "ENVOYAI_AIGW_PATH to point at a locally-built binary."
        )

    if machine in {"x86_64", "amd64"}:
        goarch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        goarch = "arm64"
    else:
        raise LocalRunError(
            f"envoyai auto-download does not support architecture "
            f"{machine!r}. Set ENVOYAI_AIGW_PATH to point at a locally-built "
            "binary."
        )

    if goos == "darwin" and goarch == "amd64":
        raise LocalRunError(
            "aigw does not publish a darwin/amd64 release binary (only "
            "darwin/arm64). Build from source at "
            "https://github.com/envoyproxy/ai-gateway and set "
            "ENVOYAI_AIGW_PATH."
        )

    return (goos, goarch)


def _release_url(version: str, goos: str, goarch: str) -> str:
    # aigw release assets are uploaded as raw binaries named
    # "aigw-<goos>-<goarch>" (see envoyproxy/ai-gateway release workflow).
    return (
        f"https://github.com/envoyproxy/ai-gateway/releases/download/"
        f"v{version}/aigw-{goos}-{goarch}"
    )


def _download(url: str, dst: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
        r.raise_for_status()
        with dst.open("wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
