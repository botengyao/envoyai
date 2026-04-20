"""Tests for Gateway.complete / acomplete / serve — the sync/async contract.

These tests guard the key promise: sync vs async is by method name, never by
a kwarg. The return type of ``complete`` is not a ``Union[Response,
Coroutine, Stream]``.
"""
from __future__ import annotations

import inspect

import pytest

import envoyai as ea


def _make_gateway() -> ea.Gateway:
    gw = ea.Gateway()
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_KEY"))("gpt-4o-mini")
    )
    return gw


def test_complete_exists_and_is_sync() -> None:
    gw = _make_gateway()
    assert callable(gw.complete)
    assert not inspect.iscoroutinefunction(gw.complete)


def test_acomplete_exists_and_is_async() -> None:
    gw = _make_gateway()
    assert callable(gw.acomplete)
    assert inspect.iscoroutinefunction(gw.acomplete)


def test_serve_exists_and_is_sync() -> None:
    gw = _make_gateway()
    assert callable(gw.serve)
    assert not inspect.iscoroutinefunction(gw.serve)


def test_complete_has_no_stream_kwarg() -> None:
    """The stream kwarg must not exist — streaming gets its own method later,
    so that return types never depend on arguments."""
    sig = inspect.signature(ea.Gateway.complete)
    assert "stream" not in sig.parameters


def test_acomplete_has_no_stream_kwarg() -> None:
    sig = inspect.signature(ea.Gateway.acomplete)
    assert "stream" not in sig.parameters


def test_complete_raises_not_implemented_today() -> None:
    gw = _make_gateway()
    with pytest.raises(NotImplementedError):
        gw.complete("chat", "hi")


def test_acomplete_raises_not_implemented_today() -> None:
    import asyncio

    gw = _make_gateway()
    with pytest.raises(NotImplementedError):
        asyncio.run(gw.acomplete("chat", "hi"))


def test_serve_raises_not_implemented_today() -> None:
    gw = _make_gateway()
    with pytest.raises(NotImplementedError):
        gw.serve()
