"""End-to-end integration: actually spawn ``aigw`` and hit it.

Opt-in via ``ENVOYAI_RUN_INTEGRATION=1`` (and ``OPENAI_API_KEY``) so the
default ``pytest`` run stays hermetic. When enabled, this test:

- runs ``Gateway.local()``, which may download ``aigw`` on first use;
- confirms the gateway answers on its port;
- round-trips a real chat completion through OpenAI.
"""
from __future__ import annotations

import os

import pytest

import envoyai as ea


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENVOYAI_RUN_INTEGRATION"),
        reason="set ENVOYAI_RUN_INTEGRATION=1 to run",
    ),
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    ),
]


def test_local_then_complete_round_trip() -> None:
    gw = ea.Gateway("envoyai-it", port=19757)
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    run = gw.local(admin_port=10640)
    try:
        resp = gw.complete("chat", "Reply with the single word: ok")
        content = resp.choices[0].message.content or ""
        assert "ok" in content.lower()
    finally:
        run.stop()
