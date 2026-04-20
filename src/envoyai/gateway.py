"""Gateway — the top-level builder users interact with.

One ``Gateway`` instance represents a single logical AI gateway. You define
models, routes, retries, and budgets in Python; the SDK produces one of four
outputs depending on what you want to do:

- ``.local()`` — run it on your laptop (no deployment target needed)
- ``.deploy()`` — one call: ship the Gateway to Kubernetes and wait for ready
- ``.render_k8s()`` — emit Kubernetes manifests for review or GitOps
- ``.apply()`` — push manifests directly to a Kubernetes cluster

The local / Client / model-building path does not require any Kubernetes
knowledge — only the ``deploy`` / ``render_k8s`` / ``apply`` / ``diff`` methods
assume a cluster.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from envoyai.auth import Header
from envoyai.errors import ConfigError, ModelNotFound
from envoyai.policy import Budget, RetryPolicy, Timeouts
from envoyai.providers.base import ModelRef

if TYPE_CHECKING:
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

        gw = envoyai.Gateway("team-a")
        gw.model("chat").route(primary=openai("gpt-4o"))
        gw.local()
    """

    def __init__(
        self,
        name: str = "default",
        *,
        port: int = 1975,
    ) -> None:
        self.name = name
        self.port = port
        self._routes: dict[str, Route] = {}
        self._cost_tracking: dict[str, Any] | None = None
        self._aliases: dict[str, str] = {}

    # --- model / route building ---------------------------------------------

    def model(self, logical_name: str) -> Route:
        """Register or fetch a logical model by name.

        Logical names are what clients send in the OpenAI-style ``model`` field.
        They need not match any provider-specific model name.
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
        raise NotImplementedError("Budget enforcement is coming in the next release.")

    # --- factories ----------------------------------------------------------

    @classmethod
    def quickstart(cls) -> "Gateway":
        """Build a Gateway from the environment — useful for the first 30s of use.

        Reads one or more of ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
        ``AZURE_OPENAI_API_KEY``, and registers a default logical model for
        each. Raises :class:`ConfigError` if none are set.
        """
        raise NotImplementedError("Gateway.quickstart() is coming in the next release.")

    # --- outputs ------------------------------------------------------------

    def local(
        self,
        *,
        admin_port: int = 1064,
        ui_port: int | None = 1976,
        debug: bool = False,
        docker: bool = False,
    ) -> "LocalRun":
        """Run the gateway locally as a background process.

        Blocks until the gateway is serving traffic, then returns a handle
        exposing ``.stop()``, ``.port``, ``.admin_port``, and ``.client()``.
        """
        raise NotImplementedError("Gateway.local() is coming in the next release.")

    def render_k8s(
        self,
        *,
        namespace: str = "default",
        kinds: list[str] | None = None,
    ) -> "RenderedManifests":
        """Emit the Kubernetes manifests needed to run this Gateway in a cluster.

        Returns a :class:`RenderedManifests` object with ``.to_yaml()`` and
        ``.write(path)``. You don't need to name the resource kinds yourself —
        the SDK picks them based on what your Gateway actually uses.
        """
        raise NotImplementedError("Gateway.render_k8s() is coming in the next release.")

    def apply(
        self,
        *,
        namespace: str = "default",
        kubeconfig: str | None = None,
        context: str | None = None,
        prune: bool = False,
    ) -> None:
        """Apply the rendered manifests directly to a Kubernetes cluster.

        Low-level counterpart to :meth:`deploy`. Prefer ``deploy()`` unless you
        want to manage the render / apply / readiness steps separately.
        """
        raise NotImplementedError("Gateway.apply() is coming in the next release.")

    def deploy(
        self,
        *,
        kubeconfig: str | None = None,
        context: str | None = None,
        namespace: str = "default",
        wait: bool = True,
        timeout: str = "5m",
    ) -> "RenderedManifests":
        """Ship this Gateway to Kubernetes in one call.

        Combines :meth:`render_k8s` + :meth:`apply` + readiness polling into a
        single opinionated flow — get from Python to a running gateway without
        writing YAML, running ``kubectl``, or chasing reconciliation status.
        Returns the same manifests :meth:`render_k8s` would have produced so
        callers can still inspect what got applied.
        """
        raise NotImplementedError("Gateway.deploy() is coming in the next release.")

    def diff(
        self,
        *,
        namespace: str = "default",
        kubeconfig: str | None = None,
        context: str | None = None,
    ) -> "RenderedManifests":
        """Show drift between this Gateway (as declared in Python) and what's
        currently deployed in a cluster."""
        raise NotImplementedError("Gateway.diff() is coming in the next release.")

    # --- internal -----------------------------------------------------------

    def _validate(self) -> None:
        """Structurally validate the Gateway, collecting every problem at once.

        Called automatically before :meth:`local`, :meth:`render_k8s`,
        :meth:`deploy`, and :meth:`apply`. Users can call it directly to fail
        fast in tests. All detected errors are reported in a single
        :class:`ConfigError` so callers fix everything in one edit cycle, not
        one-error-per-run like LiteLLM's runtime-first surprises.
        """
        problems: list[str] = []

        if not self._routes:
            problems.append(
                "gateway has no models; call .model(name).route(...) before building"
            )

        for name, route in self._routes.items():
            if route._primary is None:
                problems.append(
                    f"model '{name}' has no primary; call .route(primary=...)"
                )
            for ref in _iter_model_refs(route._primary):
                _check_ref(problems, f"model '{name}' primary", ref)
            for i, fb in enumerate(route._fallbacks):
                for ref in _iter_model_refs(fb):
                    _check_ref(problems, f"model '{name}' fallback #{i}", ref)

        for alias, target in self._aliases.items():
            if target not in self._routes:
                problems.append(
                    f"alias '{alias}' targets unknown model '{target}'; "
                    f"registered models: {sorted(self._routes)}"
                )

        if problems:
            raise ConfigError(
                "gateway configuration has {n} problem{s}:\n  - {body}".format(
                    n=len(problems),
                    s="" if len(problems) == 1 else "s",
                    body="\n  - ".join(problems),
                )
            )


def _iter_model_refs(spec: Any) -> Iterable[ModelRef]:
    """Yield every ``ModelRef`` inside a primary/fallback spec.

    Accepts either a single ``ModelRef`` or a ``Split`` dict mapping
    ``ModelRef`` to weight. Anything else is silently skipped here — the
    spec's type is caught by the route-level checks.
    """
    if spec is None:
        return
    if isinstance(spec, ModelRef):
        yield spec
        return
    if isinstance(spec, dict):
        for k in spec:
            if isinstance(k, ModelRef):
                yield k


def _check_ref(problems: list[str], where: str, ref: ModelRef) -> None:
    if not ref.model:
        problems.append(f"{where}: empty model name")
    if ref.weight < 0:
        problems.append(f"{where}: negative weight {ref.weight}")
