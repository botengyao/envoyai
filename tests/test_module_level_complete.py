"""Tests for envoyai.complete / envoyai.acomplete — the two-line shortcut.

The module-level helpers auto-register a route on a process-wide singleton
Gateway, mark the singleton as running against ``127.0.0.1:1975``, and
dispatch via the local-gateway client path. The real upstream API key
never touches the Python client — it lives in the rendered gateway config
and is injected server-side.

The OpenAI SDK is patched so these tests stay hermetic — no real HTTP.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

import envoyai as ea
from envoyai._internal.singleton import get_or_create, reset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    reset()
    yield
    reset()


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
    import openai

    sync_clients: list[_FakeSyncClient] = []
    async_clients: list[_FakeAsyncClient] = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: sync_clients.append(_FakeSyncClient(**kw)) or sync_clients[-1])
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: async_clients.append(_FakeAsyncClient(**kw)) or async_clients[-1])
    return {"sync": sync_clients, "async": async_clients}


# ---------------------------------------------------------------------------
# Public API shape
# ---------------------------------------------------------------------------


def test_complete_is_public_and_sync() -> None:
    assert callable(ea.complete)
    assert not inspect.iscoroutinefunction(ea.complete)


def test_acomplete_is_public_and_async() -> None:
    assert callable(ea.acomplete)
    assert inspect.iscoroutinefunction(ea.acomplete)


# ---------------------------------------------------------------------------
# Auto-registration + dispatch through the local gateway
# ---------------------------------------------------------------------------


def test_openai_auto_register_and_dispatch(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ea.complete(model="gpt-4o-mini", messages="hi")

    gw = get_or_create()
    assert "gpt-4o-mini" in gw._routes

    # The Python client never sees the real key. It talks to the local
    # gateway; the gateway injects the real credential upstream.
    (client,) = fake_openai["sync"]
    assert client.api_key == "unused"
    assert client.base_url == "http://127.0.0.1:1975/v1"

    (call,) = client.chat.completions.calls
    assert call["model"] == "gpt-4o-mini"
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_explicit_provider_slash_model(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ea.complete(model="openai/gpt-5", messages="hi")

    gw = get_or_create()
    assert "openai/gpt-5" in gw._routes
    (call,) = fake_openai["sync"][0].chat.completions.calls
    # The logical name goes on the wire; the gateway picks the provider.
    assert call["model"] == "openai/gpt-5"


def test_anthropic_auto_register_from_prefix(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    ea.complete(model="claude-sonnet-4", messages="hi")
    assert "claude-sonnet-4" in get_or_create()._routes
    assert fake_openai["sync"][0].base_url == "http://127.0.0.1:1975/v1"


def test_bedrock_auto_register_from_prefix(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    ea.complete(model="anthropic.claude-sonnet-4-20250514-v1:0", messages="hi")
    assert "anthropic.claude-sonnet-4-20250514-v1:0" in get_or_create()._routes


def test_cohere_auto_register_from_prefix(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "co-test")
    ea.complete(model="command-r-plus", messages="hi")
    assert "command-r-plus" in get_or_create()._routes


def test_acomplete_registers_and_awaits(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    asyncio.run(ea.acomplete(model="claude-sonnet-4", messages="hi"))
    assert "claude-sonnet-4" in get_or_create()._routes
    assert fake_openai["async"][0].base_url == "http://127.0.0.1:1975/v1"
    assert fake_openai["async"][0].api_key == "unused"


# ---------------------------------------------------------------------------
# Registration edge cases
# ---------------------------------------------------------------------------


def test_unknown_model_raises_model_not_found() -> None:
    with pytest.raises(ea.errors.ModelNotFound):
        ea.complete(model="mystery-42", messages="hi")


def test_missing_env_var_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ea.errors.ConfigError, match="OPENAI_API_KEY"):
        ea.complete(model="gpt-4o", messages="hi")


def test_azure_auto_register_points_at_explicit_gateway() -> None:
    with pytest.raises(ea.errors.ConfigError, match="explicit Gateway"):
        ea.complete(model="azure/my-deployment", messages="hi")


def test_vertex_auto_register_points_at_explicit_gateway() -> None:
    with pytest.raises(ea.errors.ConfigError, match="explicit Gateway"):
        ea.complete(model="gemini-2.5-flash", messages="hi")


def test_registration_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for _ in range(3):
        ea.complete(model="gpt-4o-mini", messages="hi")
    assert list(get_or_create()._routes).count("gpt-4o-mini") == 1
    assert len(fake_openai["sync"]) == 3


def test_reset_clears_registrations(
    monkeypatch: pytest.MonkeyPatch, fake_openai: dict[str, list[Any]]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ea.complete(model="gpt-4o-mini", messages="hi")
    assert "gpt-4o-mini" in get_or_create()._routes
    reset()
    assert "gpt-4o-mini" not in get_or_create()._routes


def test_singleton_starts_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """The singleton is marked running so ea.complete() can dispatch without
    the user calling .local() explicitly."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    gw = get_or_create()
    assert gw._running is not None
    assert gw._running.port == 1975
