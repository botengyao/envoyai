"""Tests for envoyai.complete / envoyai.acomplete — the two-line shortcut.

The functions auto-register a route on a process-wide singleton Gateway,
then call ``Gateway.complete`` / ``Gateway.acomplete``. Those raise
``NotImplementedError`` until the runtime lands, so these tests verify
the *registration* behavior and leave the final call propagating the stub.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import envoyai as ea
from envoyai._internal.singleton import get_or_create, reset


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Each test gets a fresh singleton."""
    reset()
    yield
    reset()


def test_complete_is_public_and_sync() -> None:
    assert callable(ea.complete)
    assert not inspect.iscoroutinefunction(ea.complete)


def test_acomplete_is_public_and_async() -> None:
    assert callable(ea.acomplete)
    assert inspect.iscoroutinefunction(ea.acomplete)


def test_openai_auto_register_from_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(NotImplementedError):
        ea.complete(model="gpt-4o-mini", messages="hi")
    gw = get_or_create()
    assert "gpt-4o-mini" in gw._routes


def test_anthropic_auto_register_from_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with pytest.raises(NotImplementedError):
        ea.complete(model="claude-sonnet-4", messages="hi")
    gw = get_or_create()
    assert "claude-sonnet-4" in gw._routes


def test_bedrock_auto_register_from_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with pytest.raises(NotImplementedError):
        ea.complete(model="anthropic.claude-sonnet-4-20250514-v1:0", messages="hi")
    gw = get_or_create()
    assert "anthropic.claude-sonnet-4-20250514-v1:0" in gw._routes


def test_cohere_auto_register_from_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "co-test")
    with pytest.raises(NotImplementedError):
        ea.complete(model="command-r-plus", messages="hi")
    gw = get_or_create()
    assert "command-r-plus" in gw._routes


def test_explicit_provider_slash_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(NotImplementedError):
        ea.complete(model="openai/gpt-5", messages="hi")
    gw = get_or_create()
    assert "openai/gpt-5" in gw._routes


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


def test_registration_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for _ in range(3):
        with pytest.raises(NotImplementedError):
            ea.complete(model="gpt-4o-mini", messages="hi")
    gw = get_or_create()
    assert list(gw._routes).count("gpt-4o-mini") == 1


def test_acomplete_registers_and_awaits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with pytest.raises(NotImplementedError):
        asyncio.run(ea.acomplete(model="claude-sonnet-4", messages="hi"))
    gw = get_or_create()
    assert "claude-sonnet-4" in gw._routes


def test_reset_clears_registrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(NotImplementedError):
        ea.complete(model="gpt-4o-mini", messages="hi")
    assert "gpt-4o-mini" in get_or_create()._routes
    reset()
    # Fresh singleton has no routes.
    assert "gpt-4o-mini" not in get_or_create()._routes
