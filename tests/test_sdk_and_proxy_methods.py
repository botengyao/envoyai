"""Tests for Gateway.complete / acomplete / serve — the sync/async contract.

These tests guard three promises:

1. Sync vs async is decided by method name, never by a kwarg.
2. ``complete`` / ``acomplete`` dispatch to the **local gateway** at
   ``http://127.0.0.1:<port>``. The real provider key never touches the
   Python client — that's server-side in the gateway's auth policy.
3. Calling ``complete`` before the gateway is running raises a clear
   ``ConfigError``.

The OpenAI SDK is patched so these tests stay hermetic — no real HTTP.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

import envoyai as ea
from envoyai._internal.runtime import LocalRun


# ---------------------------------------------------------------------------
# OpenAI SDK fake: records the kwargs it was constructed with and the
# payload of every chat.completions.create() call.
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "fake", "model": kwargs.get("model"), "choices": []}


class _FakeAsyncCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": "fake", "model": kwargs.get("model"), "choices": []}


class _FakeChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _FakeSyncClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, **_: Any) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.chat = _FakeChat(_FakeCompletions())


class _FakeAsyncClient:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, **_: Any) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.chat = _FakeChat(_FakeAsyncCompletions())

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Patch ``openai.OpenAI`` and ``openai.AsyncOpenAI`` to recording fakes."""
    import openai

    sync_clients: list[_FakeSyncClient] = []
    async_clients: list[_FakeAsyncClient] = []

    def sync_factory(**kwargs: Any) -> _FakeSyncClient:
        c = _FakeSyncClient(**kwargs)
        sync_clients.append(c)
        return c

    def async_factory(**kwargs: Any) -> _FakeAsyncClient:
        c = _FakeAsyncClient(**kwargs)
        async_clients.append(c)
        return c

    monkeypatch.setattr(openai, "OpenAI", sync_factory)
    monkeypatch.setattr(openai, "AsyncOpenAI", async_factory)
    return {"sync": sync_clients, "async": async_clients}


def _make_gateway(*, running: bool = True) -> ea.Gateway:
    gw = ea.Gateway()
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    if running:
        gw._running = LocalRun(port=1975, admin_port=1064)
    return gw


# ---------------------------------------------------------------------------
# sync/async contract
# ---------------------------------------------------------------------------


def test_complete_exists_and_is_sync() -> None:
    assert callable(ea.Gateway.complete)
    assert not inspect.iscoroutinefunction(ea.Gateway.complete)


def test_acomplete_exists_and_is_async() -> None:
    assert callable(ea.Gateway.acomplete)
    assert inspect.iscoroutinefunction(ea.Gateway.acomplete)


def test_serve_exists_and_is_sync() -> None:
    assert callable(ea.Gateway.serve)
    assert not inspect.iscoroutinefunction(ea.Gateway.serve)


def test_complete_has_no_stream_kwarg() -> None:
    assert "stream" not in inspect.signature(ea.Gateway.complete).parameters


def test_acomplete_has_no_stream_kwarg() -> None:
    assert "stream" not in inspect.signature(ea.Gateway.acomplete).parameters


# ---------------------------------------------------------------------------
# Dispatch through the local gateway (Envoy-backed), never direct to provider
# ---------------------------------------------------------------------------


def test_complete_dispatches_to_local_gateway(fake_openai: dict[str, list[Any]]) -> None:
    gw = _make_gateway()
    gw.complete("chat", "hi")

    (client,) = fake_openai["sync"]
    assert client.api_key == "unused"
    assert client.base_url == "http://127.0.0.1:1975"

    (call,) = client.chat.completions.calls
    assert call["model"] == "chat"  # logical name, not upstream
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_acomplete_dispatches_to_local_gateway(fake_openai: dict[str, list[Any]]) -> None:
    gw = _make_gateway()
    asyncio.run(gw.acomplete("chat", "hi"))

    (client,) = fake_openai["async"]
    assert client.api_key == "unused"
    assert client.base_url == "http://127.0.0.1:1975"

    (call,) = client.chat.completions.calls
    assert call["model"] == "chat"


def test_complete_resolves_aliases(fake_openai: dict[str, list[Any]]) -> None:
    gw = _make_gateway()
    gw.alias("gpt-4", target="chat")
    gw.complete("gpt-4", "hi")

    (client,) = fake_openai["sync"]
    (call,) = client.chat.completions.calls
    # The alias is resolved before the request leaves Python.
    assert call["model"] == "chat"


def test_complete_forwards_optional_kwargs(fake_openai: dict[str, list[Any]]) -> None:
    gw = _make_gateway()
    gw.complete(
        "chat",
        [{"role": "user", "content": "hi"}],
        temperature=0.2,
        max_tokens=64,
        tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
        tool_choice="auto",
    )
    (call,) = fake_openai["sync"][0].chat.completions.calls
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 64
    assert call["tools"][0]["function"]["name"] == "f"
    assert call["tool_choice"] == "auto"


# ---------------------------------------------------------------------------
# Build-time / preflight errors
# ---------------------------------------------------------------------------


def test_complete_requires_running_gateway(fake_openai: dict[str, list[Any]]) -> None:
    gw = _make_gateway(running=False)
    with pytest.raises(ea.errors.ConfigError, match=r"\.local\(\)"):
        gw.complete("chat", "hi")
    assert fake_openai["sync"] == []


def test_complete_rejects_unknown_model(fake_openai: dict[str, list[Any]]) -> None:
    gw = _make_gateway()
    with pytest.raises(ea.errors.ModelNotFound):
        gw.complete("not-registered", "hi")


def test_serve_with_unresolvable_aigw_path_raises_local_run_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve()`` fails fast with a clear LocalRunError when the user-supplied
    ``ENVOYAI_AIGW_PATH`` doesn't point at a real file — no subprocess, no
    auto-download attempt, no cryptic OSError."""
    monkeypatch.setenv("ENVOYAI_AIGW_PATH", "/nonexistent/aigw")
    gw = _make_gateway()
    with pytest.raises(ea.errors.LocalRunError, match="ENVOYAI_AIGW_PATH"):
        gw.serve()
