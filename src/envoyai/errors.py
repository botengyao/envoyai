"""User-facing exception hierarchy.

Every error surfaced by envoyai is one of these types. Low-level messages from
the gateway are translated into these before reaching user code.
"""
from __future__ import annotations


class EnvoyAIError(Exception):
    """Base class for all envoyai exceptions."""


class ConfigError(EnvoyAIError):
    """Invalid Gateway configuration detected at build time, before any network
    I/O or rendering. Raised by builder methods."""


class InvalidConfigError(EnvoyAIError):
    """Config was rejected by the gateway when applied or reconciled."""


class ModelNotFound(EnvoyAIError):
    """A logical model name was used that is not registered on this Gateway."""

    def __init__(self, model: str, known: list[str] | None = None):
        msg = f"model '{model}' is not registered on this gateway"
        if known:
            msg += f"; registered models: {sorted(known)}"
        super().__init__(msg)
        self.model = model


class ProviderUnavailable(EnvoyAIError):
    """A configured provider could not be reached or authenticated."""

    def __init__(self, provider: str, reason: str):
        super().__init__(f"provider '{provider}' unavailable: {reason}")
        self.provider = provider
        self.reason = reason


class BudgetExceeded(EnvoyAIError):
    """A budget enforcement threshold was crossed; request was rejected by the
    gateway."""

    def __init__(self, team: str, limit_usd: float, spent_usd: float):
        super().__init__(
            f"team '{team}' exceeded budget ${limit_usd:.2f} (spent ${spent_usd:.2f})"
        )
        self.team = team
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd


class LocalRunError(EnvoyAIError):
    """`Gateway.local()` failed to start or bootstrap the local gateway."""


class RenderError(EnvoyAIError):
    """`Gateway.render_k8s()` could not produce valid manifests."""
