"""Gateway — the top-level builder users interact with.

One ``Gateway`` instance represents a single logical AI gateway. It accumulates
model definitions, routes, cost-tracking config, and budgets, then emits one of
three outputs: a local process (``.local()``), Kubernetes manifests
(``.render_k8s()``), or a direct apply (``.apply()``).

All implementation details — CRDs, Envoy config, retry filter chains — live in
``envoyai._internal`` and are never exposed to user code.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from envoyai.auth import Header
from envoyai.errors import ConfigError, ModelNotFound
from envoyai.policy import Budget, RetryPolicy, Timeouts
from envoyai.providers.base import ModelRef

if TYPE_CHECKING:
    # Forward-declared; implemented in a later commit.
    from envoyai._internal.render import RenderedManifests
    from envoyai._internal.runtime import LocalRun


Split = dict["ModelRef", int]
"""A weight-to-ModelRef mapping used for load-split routing."""

PrimarySpec = "ModelRef | Split"
"""What can be passed as ``primary=`` to ``.route()``: a single ModelRef or a split."""


class Route:
    """A configured route for one logical model. Returned by ``Gateway.model(name)``.

    Users build routes fluently::

        gw.model("chat").route(
            primary=openai("gpt-4o"),
            fallbacks=[bedrock("anthropic.claude-sonnet-4-...")],
            retry=envoyai.RetryPolicy.rate_limit_tolerant(),
        )
    """

    def __init__(self, gateway: "Gateway", logical_model: str) -> None:
        self._gateway = gateway
        self._logical_model = logical_model
        self._primary: PrimarySpec | None = None
        self._fallbacks: list[PrimarySpec] = []
        self._retry: RetryPolicy | None = None
        self._timeouts: Timeouts | None = None
        self._budget: Budget | None = None
        self._tags: list[str] = []

    def route(
        self,
        *,
        primary: PrimarySpec,
        fallbacks: Iterable[PrimarySpec] = (),
        retry: RetryPolicy | None = None,
        timeout: str | Timeouts | None = None,
    ) -> "Route":
        """Configure primary + fallback routing for this logical model."""
        self._primary = primary
        self._fallbacks = list(fallbacks)
        self._retry = retry
        if isinstance(timeout, str):
            self._timeouts = Timeouts(request=timeout)
        elif isinstance(timeout, Timeouts):
            self._timeouts = timeout
        return self

    def budget(self, budget: Budget | None = None, /, **kwargs: Any) -> "Route":
        """Attach a spending budget to this logical model."""
        if budget is None:
            budget = Budget(**kwargs)
        elif kwargs:
            raise ConfigError("pass either a Budget object or keyword args, not both")
        self._budget = budget
        return self

    def tag(self, *tags: str) -> "Route":
        """Attach free-form tags (e.g. 'team=chat', 'env=prod') for filtering
        in the admin UI and cost reports."""
        self._tags.extend(tags)
        return self

    # Internal inspection — not part of the public API.
    def _validate(self) -> None:
        if self._primary is None:
            raise ConfigError(
                f"model '{self._logical_model}' has no primary backend; "
                "call .route(primary=...) before building the gateway"
            )


class Gateway:
    """The top-level builder.

    Example::

        gw = envoyai.Gateway("team-a", namespace="ai-gateway")
        gw.model("chat").route(primary=openai("gpt-4o"))
        gw.local()
    """

    def __init__(
        self,
        name: str = "default",
        *,
        namespace: str = "default",
        listener_port: int = 1975,
    ) -> None:
        self.name = name
        self.namespace = namespace
        self.listener_port = listener_port
        self._routes: dict[str, Route] = {}
        self._cost_tracking: dict[str, Any] | None = None
        self._aliases: dict[str, str] = {}

    # --- model / route building ---------------------------------------------

    def model(self, logical_name: str) -> Route:
        """Register or fetch a logical model by name.

        Logical names are what clients send in the OpenAI-style ``model`` field.
        They need not match any upstream model name.
        """
        if logical_name not in self._routes:
            self._routes[logical_name] = Route(self, logical_name)
        return self._routes[logical_name]

    def alias(self, alias: str, *, target: str) -> "Gateway":
        """Alias one logical model name to another (e.g. to support gradual
        migration or vendor-specific names)."""
        if target not in self._routes:
            raise ModelNotFound(target, known=list(self._routes))
        self._aliases[alias] = target
        return self

    def track_cost(
        self,
        *,
        team_from: Header | None = None,
        user_from: Header | None = None,
    ) -> "Gateway":
        """Enable per-request cost + token accounting. Dimensions (team, user)
        are read from request headers."""
        self._cost_tracking = {"team_from": team_from, "user_from": user_from}
        return self

    def budget(self, budget: Budget | None = None, /, **kwargs: Any) -> "Gateway":
        """Attach a gateway-wide budget (e.g. per-team spend cap)."""
        # Implementation note: gateway-wide budgets live alongside the route list
        # and render to rate-limiting policy. For now, stub.
        raise NotImplementedError("budget rendering lands in the next commit")

    # --- factories ----------------------------------------------------------

    @classmethod
    def quickstart(cls) -> "Gateway":
        """Build a Gateway from the environment — useful for the first 30s of use.

        Reads one or more of ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
        ``AZURE_OPENAI_API_KEY``, and registers a default logical model for
        each. Raises :class:`ConfigError` if none are set.
        """
        raise NotImplementedError("quickstart lands in the next commit")

    # --- outputs ------------------------------------------------------------

    def local(
        self,
        *,
        admin_port: int = 1064,
        ui_port: int | None = 1976,
        debug: bool = False,
        docker: bool = False,
    ) -> "LocalRun":
        """Run the gateway locally by wrapping ``aigw run``.

        Blocks until the subprocess is ready (listener bound, admin reachable),
        then returns a handle exposing ``.stop()``, ``.listener_port``,
        ``.admin_port``, and ``.client()``.
        """
        raise NotImplementedError("local() lands after render.py is wired up")

    def render_k8s(
        self,
        *,
        kinds: list[str] | None = None,
    ) -> "RenderedManifests":
        """Emit the Kubernetes manifests needed to run this Gateway in-cluster.

        Returns a :class:`RenderedManifests` object with ``.to_yaml()``,
        ``.write(path)``, and per-kind accessors. Users never need to name the
        resource kinds themselves.
        """
        raise NotImplementedError("render_k8s() lands with the CRD codegen commit")

    def apply(
        self,
        *,
        kubeconfig: str | None = None,
        context: str | None = None,
        prune: bool = False,
    ) -> None:
        """Apply the rendered manifests directly to a cluster."""
        raise NotImplementedError("apply() lands after render_k8s is live")

    def diff(
        self,
        *,
        kubeconfig: str | None = None,
        context: str | None = None,
    ) -> "RenderedManifests":
        """Show drift between this in-Python config and what's on-cluster."""
        raise NotImplementedError("diff() lands after render_k8s is live")

    # --- internal -----------------------------------------------------------

    def _validate(self) -> None:
        if not self._routes:
            raise ConfigError(
                "gateway has no models; call .model(name).route(...) before building"
            )
        for route in self._routes.values():
            route._validate()
