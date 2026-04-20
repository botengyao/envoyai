"""Tests for ``Gateway.local()`` and ``Gateway.serve()`` runtime wiring.

Unit tests here mock out the ``aigw_process`` helpers so we verify the
*glue* — rendering, writing the temp file, launching, readiness probing,
stopping — without depending on the real ``aigw`` binary. The full
end-to-end integration test lives in ``test_aigw_integration.py`` and
skips when ``aigw`` isn't on PATH.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import envoyai as ea


class _FakeProc:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.waited = False
        self._rc: int | None = None

    def poll(self) -> int | None:
        return self._rc

    def terminate(self) -> None:
        self.terminated = True
        self._rc = 0

    def kill(self) -> None:
        self.killed = True
        self._rc = -9

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self._rc if self._rc is not None else 0


def _openai_gw() -> ea.Gateway:
    gw = ea.Gateway("team-a", port=1975)
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    return gw


@pytest.fixture
def mocked_aigw(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace find_aigw / spawn / probe_ready with recording stubs."""
    import envoyai._internal.aigw_process as aigw_process

    state: dict[str, Any] = {"proc": _FakeProc(), "spawn_calls": [], "probe_calls": []}

    monkeypatch.setattr(aigw_process, "find_aigw", lambda: Path("/fake/aigw"))

    def fake_spawn(config_path: Path, **kwargs: Any) -> Any:
        state["spawn_calls"].append({"config_path": config_path, **kwargs})
        state["config_path"] = config_path
        return state["proc"]

    def fake_probe(port: int, **kwargs: Any) -> None:
        state["probe_calls"].append({"port": port, **kwargs})

    def fake_run_foreground(config_path: Path, **kwargs: Any) -> int:
        state["spawn_calls"].append({"config_path": config_path, "foreground": True, **kwargs})
        state["config_path"] = config_path
        return 0

    monkeypatch.setattr(aigw_process, "spawn_background", fake_spawn)
    monkeypatch.setattr(aigw_process, "probe_ready", fake_probe)
    monkeypatch.setattr(aigw_process, "run_foreground", fake_run_foreground)
    return state


def test_local_writes_yaml_spawns_aigw_and_probes(mocked_aigw: dict[str, Any]) -> None:
    gw = _openai_gw()
    run = gw.local()

    # LocalRun is attached and the Gateway knows it's running.
    assert run is gw._running
    assert run.port == 1975
    assert run.base_url == "http://127.0.0.1:1975"

    # aigw was spawned with a path to a real temp file we can read.
    (spawn,) = mocked_aigw["spawn_calls"]
    config_path = spawn["config_path"]
    assert config_path.exists()
    yaml_text = config_path.read_text()
    assert "AIGatewayRoute" in yaml_text
    assert "${OPENAI_API_KEY}" in yaml_text

    # Readiness was polled against the data-plane port.
    (probe,) = mocked_aigw["probe_calls"]
    assert probe["port"] == 1975


def test_local_run_stop_terminates_subprocess_and_deletes_tempfile(
    mocked_aigw: dict[str, Any],
) -> None:
    gw = _openai_gw()
    run = gw.local()
    config_path = run._config_path
    assert config_path is not None and config_path.exists()

    run.stop()

    assert mocked_aigw["proc"].terminated is True
    assert not config_path.exists()
    # Idempotent — stop() again is a no-op.
    run.stop()


def test_local_cleans_up_when_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import envoyai._internal.aigw_process as aigw_process

    proc = _FakeProc()
    written: list[Path] = []

    monkeypatch.setattr(aigw_process, "find_aigw", lambda: Path("/fake/aigw"))

    def spawn(config_path: Path, **_: Any) -> Any:
        written.append(config_path)
        return proc

    def probe(port: int, **_: Any) -> None:
        raise ea.errors.LocalRunError("timed out")

    monkeypatch.setattr(aigw_process, "spawn_background", spawn)
    monkeypatch.setattr(aigw_process, "probe_ready", probe)

    gw = _openai_gw()
    with pytest.raises(ea.errors.LocalRunError, match="timed out"):
        gw.local()

    assert gw._running is None
    assert proc.terminated is True
    # Temp file deleted so readiness failures don't leak files.
    assert all(not p.exists() for p in written)


def test_serve_forwards_to_foreground_runner(mocked_aigw: dict[str, Any]) -> None:
    gw = _openai_gw()
    rc = gw.serve()
    assert rc == 0
    (call,) = mocked_aigw["spawn_calls"]
    assert call.get("foreground") is True
    # Temp file cleaned up after serve() returns.
    assert not call["config_path"].exists()


def test_local_validates_before_spawning(mocked_aigw: dict[str, Any]) -> None:
    gw = ea.Gateway("team-a")  # no routes registered
    with pytest.raises(ea.errors.ConfigError):
        gw.local()
    # aigw was never invoked.
    assert mocked_aigw["spawn_calls"] == []
