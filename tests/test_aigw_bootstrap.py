"""Tests for the aigw auto-download / resolution chain.

Network I/O is mocked via monkeypatch — these tests never hit GitHub.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import envoyai as ea  # noqa: F401  — ensures import works
from envoyai._internal import aigw_bootstrap


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the envoyai cache into a tmp dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("ENVOYAI_AIGW_PATH", raising=False)
    # Pretend PATH is empty so we don't accidentally find a real aigw.
    monkeypatch.setattr("shutil.which", lambda _name: None)
    return tmp_path / "envoyai" / "bin"


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the HTTP download with an in-memory fake."""
    state: dict[str, Any] = {"urls": [], "payload": b"#!/fake-aigw\n"}

    def fake_dl(url: str, dst: Path) -> None:
        state["urls"].append(url)
        dst.write_bytes(state["payload"])

    monkeypatch.setattr(aigw_bootstrap, "_download", fake_dl)
    return state


# ---------------------------------------------------------------------------
# Resolution chain
# ---------------------------------------------------------------------------


def test_env_var_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "custom-aigw"
    binary.write_text("#!/fake\n")
    monkeypatch.setenv("ENVOYAI_AIGW_PATH", str(binary))
    resolved = aigw_bootstrap.resolve_binary(auto_download=False, verbose=False)
    assert resolved == binary


def test_env_var_missing_file_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVOYAI_AIGW_PATH", "/does/not/exist")
    with pytest.raises(ea.errors.LocalRunError, match="ENVOYAI_AIGW_PATH"):
        aigw_bootstrap.resolve_binary(auto_download=False, verbose=False)


def test_path_is_preferred_over_cache(
    monkeypatch: pytest.MonkeyPatch, isolated_cache: Path, tmp_path: Path
) -> None:
    system_aigw = tmp_path / "usr-local-bin-aigw"
    system_aigw.write_text("#!/fake\n")
    monkeypatch.setattr("shutil.which", lambda name: str(system_aigw) if name == "aigw" else None)
    resolved = aigw_bootstrap.resolve_binary(auto_download=False, verbose=False)
    assert resolved == system_aigw


def test_cache_hit_skips_download(
    isolated_cache: Path, fake_download: dict[str, Any]
) -> None:
    # Pre-populate the cache.
    cached = aigw_bootstrap.cached_path()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"#!/cached\n")
    cached.chmod(0o755)

    resolved = aigw_bootstrap.resolve_binary(auto_download=True, verbose=False)
    assert resolved == cached
    assert fake_download["urls"] == []  # no HTTP hit


def test_missing_with_auto_download_false_raises(isolated_cache: Path) -> None:
    with pytest.raises(ea.errors.LocalRunError, match="envoyai download-aigw"):
        aigw_bootstrap.resolve_binary(auto_download=False, verbose=False)


def test_missing_triggers_download_and_caches_executable(
    isolated_cache: Path, fake_download: dict[str, Any]
) -> None:
    resolved = aigw_bootstrap.resolve_binary(auto_download=True, verbose=False)
    assert resolved == aigw_bootstrap.cached_path()
    assert resolved.is_file()
    # Executable bit set.
    mode = resolved.stat().st_mode
    assert mode & stat.S_IXUSR
    assert resolved.read_bytes() == fake_download["payload"]
    # Fetched from the pinned release.
    assert len(fake_download["urls"]) == 1
    assert aigw_bootstrap.AIGW_VERSION in fake_download["urls"][0]
    assert fake_download["urls"][0].endswith(f"/aigw-{_expected_platform()}")


def test_download_is_idempotent(
    isolated_cache: Path, fake_download: dict[str, Any]
) -> None:
    aigw_bootstrap.ensure_downloaded(verbose=False)
    aigw_bootstrap.ensure_downloaded(verbose=False)
    assert len(fake_download["urls"]) == 1  # second call is a no-op


# ---------------------------------------------------------------------------
# Platform / error paths
# ---------------------------------------------------------------------------


def test_unsupported_os_raises(
    monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
) -> None:
    monkeypatch.setattr("platform.system", lambda: "FreeBSD")
    with pytest.raises(ea.errors.LocalRunError, match="FreeBSD"):
        aigw_bootstrap.ensure_downloaded(verbose=False)


def test_darwin_amd64_points_at_build_from_source(
    monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    with pytest.raises(ea.errors.LocalRunError, match="darwin/amd64"):
        aigw_bootstrap.ensure_downloaded(verbose=False)


def test_download_404_gives_actionable_error(
    monkeypatch: pytest.MonkeyPatch, isolated_cache: Path
) -> None:
    import httpx

    def fake_dl(url: str, dst: Path) -> None:
        raise httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("GET", url),
            response=httpx.Response(404),
        )

    monkeypatch.setattr(aigw_bootstrap, "_download", fake_dl)
    with pytest.raises(ea.errors.LocalRunError, match="Build from source"):
        aigw_bootstrap.ensure_downloaded(verbose=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_download_aigw_invokes_ensure_downloaded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from envoyai import __main__ as cli

    called: dict[str, Any] = {}

    def fake_ensure(*, version: str, verbose: bool) -> Path:
        called["version"] = version
        called["verbose"] = verbose
        return tmp_path / "aigw-stub"

    monkeypatch.setattr(
        "envoyai._internal.aigw_bootstrap.ensure_downloaded", fake_ensure
    )
    rc = cli.main(["download-aigw", "--quiet"])
    assert rc == 0
    assert called["version"] == aigw_bootstrap.AIGW_VERSION
    assert called["verbose"] is False
    out = capsys.readouterr().out.strip()
    assert out.endswith("aigw-stub")


def test_cli_where_prints_resolved_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from envoyai import __main__ as cli

    fake = tmp_path / "aigw"
    fake.write_text("#!/fake\n")
    monkeypatch.setenv("ENVOYAI_AIGW_PATH", str(fake))

    rc = cli.main(["where"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(fake)


def test_cli_version_prints_both_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from envoyai import __main__ as cli
    from envoyai import __version__

    rc = cli.main(["version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert aigw_bootstrap.AIGW_VERSION in out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expected_platform() -> str:
    """Mirror what aigw_bootstrap computes for this host, so the URL check
    works on any developer's machine."""
    import platform

    sysname = platform.system()
    machine = platform.machine().lower()
    goos = "linux" if sysname == "Linux" else "darwin"
    if machine in {"x86_64", "amd64"}:
        goarch = "amd64"
    else:
        goarch = "arm64"
    return f"{goos}-{goarch}"
