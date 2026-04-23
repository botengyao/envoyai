"""Render a :class:`envoyai.Gateway` into a multi-doc YAML that ``aigw run``
consumes.

Today's scope: API-key-based providers (OpenAI, Anthropic) with
``envoyai.env(...)`` auth, one ModelRef per primary / fallback slot, and a
single :class:`RetryPolicy` per Gateway. Fallback chains render as
prioritized ``backendRefs`` on the AIGatewayRoute rule and (when fallbacks
or a retry policy exist) a ``BackendTrafficPolicy`` attached to the
generated HTTPRoute.

Secrets use ``${VAR}`` placeholders so ``aigw run`` resolves them via
``envsubst`` at startup and the real key never lives on disk unencrypted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import yaml

from envoyai.auth import EnvVar
from envoyai.policy import RetryPolicy
from envoyai.providers.anthropic import Anthropic
from envoyai.providers.base import ModelRef
from envoyai.providers.openai import OpenAI


__all__ = ["render_yaml", "render_resources"]


# ---------------------------------------------------------------------------
# Per-provider spec — everything that differs between API-key providers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ApiKeyProviderSpec:
    """Metadata the renderer needs to emit resources for one API-key provider.

    The shared shape — ``AIServiceBackend`` + ``BackendSecurityPolicy`` +
    ``Backend`` + ``BackendTLSPolicy`` + ``Secret`` — is identical across
    these providers; only ``schema``, ``security_type``, the security
    sub-field name, and the default upstream hostname change.
    """

    schema: str
    security_type: str
    security_subfield: str
    default_hostname: str
    backend_slug: str


_API_KEY_PROVIDERS: dict[type, _ApiKeyProviderSpec] = {
    OpenAI: _ApiKeyProviderSpec(
        schema="OpenAI",
        security_type="APIKey",
        security_subfield="apiKey",
        default_hostname="api.openai.com",
        backend_slug="openai",
    ),
    Anthropic: _ApiKeyProviderSpec(
        schema="Anthropic",
        security_type="AnthropicAPIKey",
        security_subfield="anthropicAPIKey",
        default_hostname="api.anthropic.com",
        backend_slug="anthropic",
    ),
}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def render_yaml(gateway: Any, *, namespace: str = "default") -> str:
    """Return a multi-doc YAML string suitable for ``aigw run <path>``."""
    resources = render_resources(gateway, namespace=namespace)
    return yaml.safe_dump_all(resources, sort_keys=False)


def render_resources(gateway: Any, *, namespace: str = "default") -> list[dict[str, Any]]:
    """Return the list of manifest dicts that make up the gateway."""
    if not gateway._routes:
        raise _not_supported(
            "gateway has no models; call .model(name).route(primary=...) before rendering"
        )

    gateway_name = gateway.name or "envoyai-default"
    listener_port = gateway.port
    backends: dict[str, Any] = {}  # backend name → provider instance
    rule_docs: list[dict[str, Any]] = []
    any_fallbacks = False
    retry_policy: RetryPolicy | None = None

    for logical, route in gateway._routes.items():
        _reject_unsupported_route(logical, route)

        primary = route._primary
        assert isinstance(primary, ModelRef)
        primary_backend = _register_backend(gateway_name, primary.provider, backends)

        fallback_pairs: list[tuple[ModelRef, str]] = []
        for fb in route._fallbacks:
            assert isinstance(fb, ModelRef)  # Split is rejected above
            fb_backend = _register_backend(gateway_name, fb.provider, backends)
            fallback_pairs.append((fb, fb_backend))

        rule_docs.append(
            _route_rule(logical, primary, primary_backend, fallback_pairs)
        )

        if fallback_pairs:
            any_fallbacks = True

        if route._retry is not None:
            if retry_policy is not None and route._retry != retry_policy:
                raise _not_supported(
                    f"model '{logical}' has a different RetryPolicy than an earlier "
                    "route; one RetryPolicy per Gateway is the current limit"
                )
            retry_policy = route._retry

    resources: list[dict[str, Any]] = []
    resources.append(_aigateway_route(gateway_name, namespace, rule_docs))
    for backend_name, provider in backends.items():
        resources.extend(_provider_resources(backend_name, namespace, provider))
    if retry_policy is not None or any_fallbacks:
        resources.append(
            _backend_traffic_policy(gateway_name, namespace, retry_policy)
        )
    resources.append(_gateway_cr(gateway_name, namespace, listener_port))
    return resources


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _reject_unsupported_route(logical: str, route: Any) -> None:
    if route._primary is None:
        raise _not_supported(f"model '{logical}' has no primary backend")
    if not isinstance(route._primary, ModelRef):
        raise _not_supported(
            f"model '{logical}' uses a weighted Split for primary; the aigw "
            "renderer only supports a single ModelRef per primary today"
        )
    for i, fb in enumerate(route._fallbacks):
        if not isinstance(fb, ModelRef):
            raise _not_supported(
                f"model '{logical}' fallback #{i} uses a weighted Split; the "
                "aigw renderer only supports a single ModelRef per fallback today"
            )
    if route._budget is not None:
        raise _not_supported(
            f"model '{logical}' has a Budget; budget rendering lands later"
        )
    if route._timeouts is not None:
        raise _not_supported(
            f"model '{logical}' has custom Timeouts; timeout rendering lands later"
        )


def _reject_unsupported_provider(provider: Any) -> _ApiKeyProviderSpec:
    """Return the renderer spec for ``provider`` or raise a clear error."""
    spec = _API_KEY_PROVIDERS.get(type(provider))
    if spec is None:
        raise _not_supported(
            f"provider type {type(provider).__name__} is not wired into the "
            "aigw renderer yet; supported today: "
            + ", ".join(sorted(p.__name__ for p in _API_KEY_PROVIDERS))
        )
    if not isinstance(provider.api_key, EnvVar):
        raise _not_supported(
            f"{type(provider).__name__}.api_key must be envoyai.env(...) for "
            "the aigw renderer; SecretRef / InlineKey support lands later"
        )
    return spec


def _not_supported(msg: str) -> NotImplementedError:
    return NotImplementedError(
        f"aigw renderer: {msg}. Build a narrower Gateway or wait for the "
        "next release."
    )


# ---------------------------------------------------------------------------
# Backend registration
# ---------------------------------------------------------------------------


def _register_backend(
    gateway_name: str, provider: Any, backends: dict[str, Any]
) -> str:
    """Ensure ``provider`` has an entry in ``backends`` and return its backend name.

    Providers that compare equal (same type, same api_key, same base_url,
    same explicit ``.name``) collapse onto one backend — emitting the
    AIServiceBackend / Backend / Secret / BSP / BTLS stack once — so a
    primary and a fallback pointing at the same upstream share one backend.
    """
    spec = _reject_unsupported_provider(provider)
    name = _backend_name(gateway_name, provider, spec)
    backends.setdefault(name, provider)
    return name


# ---------------------------------------------------------------------------
# Resource builders
# ---------------------------------------------------------------------------


def _backend_name(
    gateway_name: str, provider: Any, spec: _ApiKeyProviderSpec
) -> str:
    return provider.name or f"{gateway_name}-{spec.backend_slug}"


def _route_rule(
    logical: str,
    primary_ref: ModelRef,
    primary_backend: str,
    fallback_pairs: list[tuple[ModelRef, str]],
) -> dict[str, Any]:
    """Build one rule on the AIGatewayRoute.

    With no fallbacks the rule has a single backendRef (no ``priority``
    field — the Gateway API's implicit default). With fallbacks every
    backendRef gets an explicit ``priority`` (primary = 0, first fallback =
    1, etc.) so aigw's retry walk is unambiguous.
    """
    primary_entry = _backend_ref(logical, primary_ref, primary_backend)
    if not fallback_pairs:
        backend_refs: list[dict[str, Any]] = [primary_entry]
    else:
        primary_entry["priority"] = 0
        backend_refs = [primary_entry]
        for i, (ref, name) in enumerate(fallback_pairs, start=1):
            entry = _backend_ref(logical, ref, name)
            entry["priority"] = i
            backend_refs.append(entry)
    return {
        "matches": [
            {
                "headers": [
                    {"type": "Exact", "name": "x-ai-eg-model", "value": logical},
                ],
            },
        ],
        "backendRefs": backend_refs,
    }


def _backend_ref(
    logical: str, ref: ModelRef, backend_name: str
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": backend_name}
    if ref.override:
        entry["modelNameOverride"] = ref.override
    elif ref.model != logical:
        entry["modelNameOverride"] = ref.model
    return entry


def _aigateway_route(
    gateway_name: str, namespace: str, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "apiVersion": "aigateway.envoyproxy.io/v1beta1",
        "kind": "AIGatewayRoute",
        "metadata": {"name": gateway_name, "namespace": namespace},
        "spec": {
            "parentRefs": [
                {
                    "name": gateway_name,
                    "kind": "Gateway",
                    "group": "gateway.networking.k8s.io",
                }
            ],
            "rules": rules,
        },
    }


def _provider_resources(
    backend_name: str, namespace: str, provider: Any
) -> list[dict[str, Any]]:
    spec = _API_KEY_PROVIDERS[type(provider)]
    hostname = _hostname(provider.base_url, default=spec.default_hostname)
    secret_name = f"{backend_name}-apikey"
    env_var = provider.api_key
    assert isinstance(env_var, EnvVar)
    return [
        {
            "apiVersion": "aigateway.envoyproxy.io/v1beta1",
            "kind": "AIServiceBackend",
            "metadata": {"name": backend_name, "namespace": namespace},
            "spec": {
                "schema": {"name": spec.schema},
                "backendRef": {
                    "name": backend_name,
                    "kind": "Backend",
                    "group": "gateway.envoyproxy.io",
                },
            },
        },
        {
            "apiVersion": "aigateway.envoyproxy.io/v1beta1",
            "kind": "BackendSecurityPolicy",
            "metadata": {"name": secret_name, "namespace": namespace},
            "spec": {
                "targetRefs": [
                    {
                        "group": "aigateway.envoyproxy.io",
                        "kind": "AIServiceBackend",
                        "name": backend_name,
                    }
                ],
                "type": spec.security_type,
                spec.security_subfield: {
                    "secretRef": {"name": secret_name, "namespace": namespace},
                },
            },
        },
        {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "Backend",
            "metadata": {"name": backend_name, "namespace": namespace},
            "spec": {"endpoints": [{"fqdn": {"hostname": hostname, "port": 443}}]},
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1alpha3",
            "kind": "BackendTLSPolicy",
            "metadata": {"name": f"{backend_name}-tls", "namespace": namespace},
            "spec": {
                "targetRefs": [
                    {
                        "group": "gateway.envoyproxy.io",
                        "kind": "Backend",
                        "name": backend_name,
                    }
                ],
                "validation": {
                    "wellKnownCACertificates": "System",
                    "hostname": hostname,
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": secret_name, "namespace": namespace},
            "type": "Opaque",
            "stringData": {"apiKey": "${" + env_var.var + "}"},
        },
    ]


def _gateway_cr(gateway_name: str, namespace: str, port: int) -> dict[str, Any]:
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "Gateway",
        "metadata": {"name": gateway_name, "namespace": namespace},
        "spec": {
            "gatewayClassName": "envoy-ai-gateway",
            "listeners": [
                {
                    "name": "http",
                    "protocol": "HTTP",
                    "port": port,
                    "allowedRoutes": {"namespaces": {"from": "All"}},
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# BackendTrafficPolicy (retry + failover)
# ---------------------------------------------------------------------------


# Per-reason translation into Envoy retry-on triggers + HTTP status codes.
# Keys are :data:`envoyai.policy.RetryReason` values. ``envoy-ratelimited``
# is emitted for rate_limit because Envoy raises it when an upstream sends
# 429 or RateLimit-Reset headers; ``retriable-status-codes`` is required to
# actually retry on anything listed in ``httpStatusCodes``.
_REASON_MAP: dict[str, tuple[list[str], list[int]]] = {
    "rate_limit": (["envoy-ratelimited", "retriable-status-codes"], [429]),
    "server_error": (["retriable-status-codes"], [500, 502, 503, 504]),
    "timeout": (["retriable-status-codes"], [504]),
    "connection_error": (["connect-failure", "reset", "refused-stream"], []),
}


def _backend_traffic_policy(
    gateway_name: str,
    namespace: str,
    policy: RetryPolicy | None,
) -> dict[str, Any]:
    """Emit the BTP that carries retry + priority-based failover.

    When ``policy is None`` but the caller still requested this document
    (i.e. fallbacks are declared without an explicit retry), we emit a
    sane failover default: one attempt per priority, retry on connect
    errors and 5xx.
    """
    effective = policy if policy is not None else _default_failover_policy()
    retry_on = _retry_on(effective)
    retry_spec: dict[str, Any] = {
        "numAttemptsPerPriority": effective.attempts_per_step,
        "numRetries": max(effective.attempts - 1, 0),
        "perRetry": {
            "backOff": {
                "baseInterval": effective.backoff_base,
                "maxInterval": effective.backoff_max,
            },
            "timeout": effective.per_retry_timeout,
        },
        "retryOn": retry_on,
    }
    return {
        "apiVersion": "gateway.envoyproxy.io/v1alpha1",
        "kind": "BackendTrafficPolicy",
        "metadata": {"name": gateway_name, "namespace": namespace},
        "spec": {
            "targetRefs": [
                {
                    "group": "gateway.networking.k8s.io",
                    "kind": "HTTPRoute",
                    # aigw generates an HTTPRoute with the same name as the
                    # AIGatewayRoute.
                    "name": gateway_name,
                }
            ],
            "retry": retry_spec,
        },
    }


def _default_failover_policy() -> RetryPolicy:
    """Retry envelope used when fallbacks exist but the user set no RetryPolicy.

    One shot per priority (so each backend in the chain gets exactly one
    try), retry on connection errors and 5xx so the failover actually
    fires.
    """
    return RetryPolicy(
        attempts=3,
        attempts_per_step=1,
        on=["connection_error", "server_error"],
    )


def _retry_on(policy: RetryPolicy) -> dict[str, Any]:
    triggers: list[str] = []
    status_codes: list[int] = []
    seen_triggers: set[str] = set()
    seen_codes: set[int] = set()
    for reason in policy.on:
        r_triggers, r_codes = _REASON_MAP[reason]
        for t in r_triggers:
            if t not in seen_triggers:
                seen_triggers.add(t)
                triggers.append(t)
        for c in r_codes:
            if c not in seen_codes:
                seen_codes.add(c)
                status_codes.append(c)
    out: dict[str, Any] = {}
    if status_codes:
        out["httpStatusCodes"] = status_codes
    if triggers:
        out["triggers"] = triggers
    # When the user picks an empty ``on`` list (e.g. RetryPolicy.fail_fast),
    # aigw still requires at least one field under retryOn. Emit a trigger
    # that effectively never fires on normal traffic rather than failing at
    # render time — the numRetries=0 from attempts=1 keeps it inert anyway.
    if not out:
        out["triggers"] = ["reset"]
    return out


def _hostname(base_url: str, *, default: str) -> str:
    parsed = urlparse(base_url)
    return parsed.hostname or default
