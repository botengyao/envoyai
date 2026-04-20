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

from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence, Union

from envoyai._internal.runtime import LocalRun
from envoyai.auth import Header
from envoyai.errors import ConfigError, ModelNotFound
from envoyai.policy import Budget, Privacy, RetryPolicy, Timeouts
from envoyai.providers.base import ModelRef

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion

    from envoyai._internal.render import RenderedManifests


ChatMessage = Mapping[str, Any]
"""An OpenAI-format chat message, e.g. ``{"role": "user", "content": "hi"}``.

Structural typing — any mapping with the right keys works. Users who want the
full typed shape can import :class:`openai.types.chat.ChatCompletionMessageParam`.
"""


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
        self._privacy: Privacy = Privacy()
        self._running: LocalRun | None = None

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

    def privacy(self, policy: Privacy) -> "Gateway":
        """Override the gateway's logging/redaction defaults.

        Defaults are safe: ``Privacy(redact_auth=True, log_prompts=False,
        log_responses=False)``. Call this only if you need content in logs.
        """
        self._privacy = policy
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

    # --- calls (SDK mode) ---------------------------------------------------

    def complete(
        self,
        model: str,
        messages: Union[str, Sequence[ChatMessage]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Union[str, Mapping[str, Any], None] = None,
        provider_options: Mapping[str, Any] | None = None,
        timeout: str | None = None,
    ) -> ChatCompletion:
        """Send one chat completion synchronously through this gateway.

        **Sync/async contract.** This method is sync; :meth:`acomplete` is
        async. The return type is always a single ``ChatCompletion`` — never
        a coroutine, never a stream, never a union. Kwargs do not change the
        return type.

        **Streaming.** Not supported by this method on purpose (return types
        would depend on kwargs). For streaming today, use the OpenAI SDK
        against the gateway's URL directly; a dedicated streaming API will
        land as a separate method.

        **Messages.** Accepts either an OpenAI-format message sequence or a
        bare string; a string is auto-wrapped as ``[{"role": "user",
        "content": <s>}]``.

        **Provider-specific knobs.** Go in ``provider_options``. Unknown keys
        raise :class:`envoyai.errors.ConfigError`; they are never silently
        dropped.

        **Prereq.** The gateway must be running — call :meth:`local` first,
        or have a gateway running at the configured URL.

        Raises
        ------
        envoyai.errors.ModelNotFound
            ``model`` isn't registered on this Gateway.
        envoyai.errors.ProviderUnavailable
            Every configured provider (primary + fallbacks) failed.
        envoyai.errors.RateLimited
            Provider rate-limited the request; ``retry_after_s`` is
            populated on the exception.
        envoyai.errors.BudgetExceeded
            A budget with ``enforce_at`` was crossed.
        """
        from envoyai._internal.dispatch import dispatch_sync

        run = self._require_running("complete")
        logical = self._resolve_logical_model(
            model, provider_options=provider_options, timeout=timeout
        )
        return dispatch_sync(
            run,
            logical,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def acomplete(
        self,
        model: str,
        messages: Union[str, Sequence[ChatMessage]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Union[str, Mapping[str, Any], None] = None,
        provider_options: Mapping[str, Any] | None = None,
        timeout: str | None = None,
    ) -> ChatCompletion:
        """Send one chat completion asynchronously through this gateway.

        **Sync/async contract.** This coroutine must be awaited and resolves
        to the same ``ChatCompletion`` type that :meth:`complete` returns.
        Sync vs async is determined entirely by which method you call —
        never by a kwarg.

        See :meth:`complete` for argument, error, and streaming semantics.
        """
        from envoyai._internal.dispatch import dispatch_async

        run = self._require_running("acomplete")
        logical = self._resolve_logical_model(
            model, provider_options=provider_options, timeout=timeout
        )
        return await dispatch_async(
            run,
            logical,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )

    # --- outputs ------------------------------------------------------------

    def local(
        self,
        *,
        admin_port: int = 1064,
        debug: bool = False,
        ready_timeout_s: float = 20.0,
    ) -> LocalRun:
        """Run the gateway locally as a **background** process (SDK mode).

        Renders the Gateway to an ``aigw``-compatible multi-doc YAML,
        writes it to a temp file, spawns ``aigw run`` pointed at that
        file, and polls until the HTTP port is accepting requests. Once
        ready, marks ``self._running`` and returns the handle.

        The calling Python process stays free to make calls via
        :meth:`complete` / :meth:`acomplete`, or via any OpenAI-compatible
        client pointed at ``http://127.0.0.1:<port>``.

        For the foreground / long-running service use case (where the same
        process *is* the proxy), use :meth:`serve`.

        Requires the ``aigw`` binary on PATH.
        """
        from envoyai._internal import aigw_process
        from envoyai._internal.render.aigw_standalone import render_yaml

        self._validate()
        yaml_text = render_yaml(self)
        config_path = aigw_process.write_config(yaml_text)
        proc = aigw_process.spawn_background(
            config_path, admin_port=admin_port, debug=debug
        )
        try:
            aigw_process.probe_ready(self.port, timeout_s=ready_timeout_s)
        except Exception:
            aigw_process.stop_background(proc)
            config_path.unlink(missing_ok=True)
            raise
        run = LocalRun(
            port=self.port,
            admin_port=admin_port,
            _proc=proc,
            _config_path=config_path,
        )
        self._running = run
        return run

    def serve(
        self,
        *,
        admin_port: int = 1064,
        debug: bool = False,
    ) -> int:
        """Run the gateway in the **foreground** until signaled (Proxy mode).

        Blocks the calling process; ``aigw`` receives SIGINT / SIGTERM
        directly from the shell so Ctrl-C shuts it down cleanly. Returns
        ``aigw``'s exit code when it terminates.

        For the background / same-process use case, use :meth:`local`.

        Requires the ``aigw`` binary on PATH.
        """
        from envoyai._internal import aigw_process
        from envoyai._internal.render.aigw_standalone import render_yaml

        self._validate()
        yaml_text = render_yaml(self)
        config_path = aigw_process.write_config(yaml_text)
        try:
            return aigw_process.run_foreground(
                config_path, admin_port=admin_port, debug=debug
            )
        finally:
            config_path.unlink(missing_ok=True)

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

    def _require_running(self, call_name: str) -> LocalRun:
        """Return the active :class:`LocalRun` or raise a clear ConfigError."""
        if self._running is None:
            raise ConfigError(
                f"gateway is not running; call .local() (SDK mode) or "
                f".serve() (proxy mode) before .{call_name}(). For the "
                "implicit-singleton path, envoyai.complete() manages this "
                "for you."
            )
        return self._running

    def _resolve_logical_model(
        self,
        model: str,
        *,
        provider_options: Mapping[str, Any] | None,
        timeout: str | None,
    ) -> str:
        """Resolve aliases and verify the model is registered.

        Returns the logical name to send to the gateway. The gateway itself
        picks the provider, performs any fallback walk, and applies the
        Route's retry / budget / timeout policies — we never replicate that
        logic in Python.
        """
        logical = self._aliases.get(model, model)
        if logical not in self._routes:
            raise ModelNotFound(logical, known=list(self._routes))
        if provider_options:
            raise NotImplementedError(
                "provider_options pass-through is not wired into the minimal "
                "SDK call path yet"
            )
        if timeout is not None:
            raise NotImplementedError(
                "per-call timeout is not wired into the minimal SDK call path yet"
            )
        return logical

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
