"""Retry, budget, and timeout policies.

These are user-facing intent objects. The renderer translates them into the
appropriate Envoy Gateway policies (BackendTrafficPolicy, rate limiting, etc.)
— users never see that layer.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RetryTrigger = Literal[
    "connect-failure",
    "retriable-status-codes",
    "reset",
    "retriable-4xx",
    "gateway-error",
]


class RetryPolicy(BaseModel):
    """How failed requests are retried and how failover walks the fallback chain.

    ``attempts_per_priority=1`` makes ``fallbacks=[...]`` behave as "try primary
    once, then move to the next fallback" — the intuitive default. Raise it to
    retry a given backend multiple times before moving on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempts: int = Field(default=3, ge=1, le=20)
    attempts_per_priority: int = Field(default=1, ge=1)
    per_retry_timeout: str = "30s"
    backoff_base: str = "100ms"
    backoff_max: str = "10s"
    on_status: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    on_triggers: list[RetryTrigger] = Field(
        default_factory=lambda: ["connect-failure", "retriable-status-codes"]
    )

    @classmethod
    def rate_limit_tolerant(cls) -> RetryPolicy:
        """Preset for rate-limited providers: more attempts, longer timeout."""
        return cls(attempts=5, on_status=[429, 503], per_retry_timeout="60s")

    @classmethod
    def fail_fast(cls) -> RetryPolicy:
        """Preset for latency-sensitive paths: no retries, single attempt."""
        return cls(attempts=1)

    @classmethod
    def none(cls) -> RetryPolicy:
        """Preset that disables retries and failover triggers entirely."""
        return cls(attempts=1, on_status=[], on_triggers=[])


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
    """Per-route timeouts. `request` is client-visible; `backend` caps the
    upstream call (defaults to `request` if unset)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: str = "60s"
    backend: str | None = None
