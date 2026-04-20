"""Retry, budget, and timeout policies.

User-facing intent objects. The SDK translates them into the right gateway-side
configuration at render or run time — users never see that layer.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RetryReason = Literal[
    "rate_limit",         # HTTP 429 or equivalent per-provider throttling
    "timeout",             # request exceeded per_retry_timeout
    "server_error",        # 5xx responses
    "connection_error",    # connect / reset / DNS failures
]
"""High-level reasons a request might be retried.

These are product concepts, not wire-level triggers. The SDK maps each reason
onto the appropriate mix of HTTP status codes and transport-level triggers at
render time.
"""


class RetryPolicy(BaseModel):
    """How failed requests are retried and how failover walks the fallback chain.

    ``attempts_per_step=1`` makes ``fallbacks=[...]`` behave as "try primary
    once, then move to the next fallback" — the intuitive default. Raise it to
    retry a given provider multiple times before moving on.

    ``on`` is a list of product-level reasons to retry on. Pick from
    ``rate_limit``, ``timeout``, ``server_error``, ``connection_error``.
    Presets (:meth:`rate_limit_tolerant`, :meth:`fail_fast`, :meth:`none`)
    configure this for you.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempts: int = Field(default=3, ge=1, le=20)
    attempts_per_step: int = Field(default=1, ge=1)
    per_retry_timeout: str = "30s"
    backoff_base: str = "100ms"
    backoff_max: str = "10s"
    on: list[RetryReason] = Field(
        default_factory=lambda: ["rate_limit", "timeout", "server_error", "connection_error"]
    )

    @classmethod
    def rate_limit_tolerant(cls) -> RetryPolicy:
        """Preset for rate-limited providers: more attempts, longer timeout."""
        return cls(attempts=5, on=["rate_limit", "server_error"], per_retry_timeout="60s")

    @classmethod
    def fail_fast(cls) -> RetryPolicy:
        """Preset for latency-sensitive paths: no retries, single attempt."""
        return cls(attempts=1, on=[])

    @classmethod
    def none(cls) -> RetryPolicy:
        """Preset that disables retries entirely."""
        return cls(attempts=1, on=[])


class Budget(BaseModel):
    """Spending limit with alerting and optional enforcement.

    ``alert_at`` fires a webhook / admin notification when reached.
    ``enforce_at`` installs a gateway-level rate limit when reached; if None,
    the budget is alert-only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    monthly_usd: float | None = None
    daily_usd: float | None = None
    team: str | None = None
    user: str | None = None
    alert_at: float = Field(default=0.8, ge=0.0, le=1.0)
    enforce_at: float | None = Field(default=None, ge=0.0, le=1.0)


class Timeouts(BaseModel):
    """Per-route timeouts. ``request`` is the end-to-end limit visible to the
    client; ``provider`` caps each call to a provider (defaults to ``request``
    when unset)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: str = "60s"
    provider: str | None = None


class Privacy(BaseModel):
    """Control what the gateway is allowed to log or forward to observability.

    Defaults are deliberately safe: auth headers are redacted; prompts and
    responses are logged as metadata (token counts, latency, cost) but their
    **content** is not exported to logs or callbacks. Flip the toggles only
    when you have a concrete reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    redact_auth: bool = True
    """Strip ``Authorization``, ``api-key``, ``x-api-key`` and similar
    request/response headers from logs, traces, and callbacks."""

    log_prompts: bool = False
    """When True, request body content (prompts, messages, tools) is logged
    in full. When False (default), only metadata is logged."""

    log_responses: bool = False
    """When True, response body content (completions) is logged in full.
    When False (default), only metadata is logged."""

