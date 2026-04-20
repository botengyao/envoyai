"""User-facing exception hierarchy.

Every error surfaced by envoyai is one of these types. Low-level messages from
the gateway are translated into these before reaching user code.

Each exception carries **structured fields**, not just a message string, so
code can react to failures without regex-matching on messages. LiteLLM's
exception classes have optional attributes that are sometimes set and
sometimes None depending on the code path — we require them in constructors
so that ``except RateLimited as e: sleep(e.retry_after_s)`` always works.

The original exception that caused the failure, if any, is available as
``.cause`` for debugging without leaking raw provider SDK types into code
that wants to stay provider-agnostic.
"""
from __future__ import annotations


class EnvoyAIError(Exception):
    """Base class for all envoyai exceptions.

    ``cause`` points to the underlying provider/library exception when this
    error was raised while translating one. It's informational — code should
    branch on subclasses, not on ``cause``.
    """

    cause: BaseException | None = None

    def __init__(self, message: str, *, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause


class ConfigError(EnvoyAIError):
    """Invalid Gateway configuration detected at build time, before any network
    I/O. Raised by builder methods and :meth:`Gateway._validate`."""


class InvalidConfigError(EnvoyAIError):
    """Config was rejected by the gateway when applied or reconciled."""


class ModelNotFound(EnvoyAIError):
    """A logical model name was used that is not registered on this Gateway."""

    def __init__(
        self,
        model: str,
        *,
        known: list[str] | None = None,
        cause: BaseException | None = None,
    ):
        msg = f"model '{model}' is not registered on this gateway"
        if known:
            msg += f"; registered: {sorted(known)}"
        super().__init__(msg, cause=cause)
        self.model = model
        self.known = list(known) if known else []


class ProviderUnavailable(EnvoyAIError):
    """A configured provider could not be reached or authenticated."""

    def __init__(
        self,
        *,
        provider: str,
        model: str | None = None,
        reason: str,
        trace_id: str | None = None,
        cause: BaseException | None = None,
    ):
        super().__init__(
            f"provider '{provider}' unavailable: {reason}", cause=cause
        )
        self.provider = provider
        self.model = model
        self.reason = reason
        self.trace_id = trace_id


class RateLimited(EnvoyAIError):
    """The upstream provider rejected the request with a rate-limit response.

    ``retry_after_s`` is the number of seconds the provider suggested waiting
    before retrying. It is always populated (0.0 when the provider gave no
    hint), so callers never need to handle a ``None``.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        retry_after_s: float = 0.0,
        trace_id: str | None = None,
        cause: BaseException | None = None,
    ):
        msg = f"provider '{provider}' rate-limited model '{model}'"
        if retry_after_s > 0:
            msg += f" (retry after {retry_after_s:g}s)"
        super().__init__(msg, cause=cause)
        self.provider = provider
        self.model = model
        self.retry_after_s = retry_after_s
        self.trace_id = trace_id


class BudgetExceeded(EnvoyAIError):
    """A budget enforcement threshold was crossed; request was rejected by
    the gateway."""

    def __init__(
        self,
        *,
        team: str,
        limit_usd: float,
        spent_usd: float,
        trace_id: str | None = None,
        cause: BaseException | None = None,
    ):
        super().__init__(
            f"team '{team}' exceeded budget ${limit_usd:.2f} "
            f"(spent ${spent_usd:.2f})",
            cause=cause,
        )
        self.team = team
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd
        self.trace_id = trace_id


class LocalRunError(EnvoyAIError):
    """``Gateway.local()`` failed to start or bootstrap the local gateway."""


class RenderError(EnvoyAIError):
    """``Gateway.render_k8s()`` could not produce valid manifests."""


class DeployError(EnvoyAIError):
    """``Gateway.deploy()`` / ``Gateway.apply()`` could not apply manifests
    or reach cluster readiness within the timeout."""
