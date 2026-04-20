"""10 — Custom retry policy.

Retry on specific product-level reasons rather than status codes: the SDK
maps each reason to the right mix of HTTP status and transport triggers.
"""
from __future__ import annotations

import envoyai as ea

openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))

gw = ea.Gateway()
gw.model("chat").route(
    primary=openai("gpt-4o"),
    retry=ea.RetryPolicy(
        attempts=5,
        on=["rate_limit", "server_error"],   # skip timeouts / conn errors
        per_retry_timeout="45s",
        backoff_base="200ms",
        backoff_max="15s",
    ),
)

# Presets cover the common cases:
#   ea.RetryPolicy.rate_limit_tolerant()   — 5 attempts, 60s per-retry
#   ea.RetryPolicy.fail_fast()             — single attempt, no retries
#   ea.RetryPolicy.none()                  — disables all retries

gw.local()
