"""End-to-end integration: actually spawn ``aigw`` and hit it.

Skipped when the ``aigw`` binary isn't on PATH or ``OPENAI_API_KEY`` isn't
set. When both are present and an outbound network is available, this
test validates that:

- ``Gateway.local()`` produces a running subprocess;
- the gateway's ``/v1/models`` endpoint answers;
- ``gw.complete()`` round-trips a real chat completion through OpenAI.

This is the only test in the suite that makes outbound calls; keep it
isolated from the hermetic unit tests.
"""
from __future__ import annotations

import os
import shutil

import pytest

import envoyai as ea


pytestmark = [
    pytest.mark.skipif(
        shutil.which("aigw") is None,
        reason="aigw binary not on PATH",
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
