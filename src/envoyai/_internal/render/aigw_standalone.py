"""Render a :class:`envoyai.Gateway` into a multi-doc YAML that ``aigw run``
consumes.

Today's scope: one provider per route, primary only (no fallbacks, no Split,
no retry/budget/timeouts), and only the API-key-based providers — OpenAI and
Anthropic — both with ``envoyai.env(...)`` auth. Anything beyond that raises
:class:`NotImplementedError` with a clear pointer rather than silently doing
less than the user asked for.

Secrets use ``${VAR}`` placeholders so ``aigw run`` resolves them via
``envsubst`` at startup and the real key never lives on disk unencrypted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import yaml

from envoyai.auth import EnvVar
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

    for logical, route in gateway._routes.items():
        _reject_unsupported_route(logical, route)
        primary = route._primary
        assert isinstance(primary, ModelRef)
        provider = primary.provider
        spec = _reject_unsupported_provider(provider)
        backend_name = _backend_name(gateway_name, provider, spec)
        backends[backend_name] = provider
        rule_docs.append(_route_rule(logical, primary, backend_name))

    resources: list[dict[str, Any]] = []
    resources.append(_aigateway_route(gateway_name, namespace, rule_docs))
    for backend_name, provider in backends.items():
        resources.extend(_provider_resources(backend_name, namespace, provider))
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
    if route._fallbacks:
        raise _not_supported(
            f"model '{logical}' has fallbacks; the aigw renderer does not "
            "emit the required BackendTrafficPolicy yet"
        )
    if route._retry is not None:
        raise _not_supported(
            f"model '{logical}' has a RetryPolicy; retry rendering lands later"
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
# Resource builders
# ---------------------------------------------------------------------------


def _backend_name(
    gateway_name: str, provider: Any, spec: _ApiKeyProviderSpec
) -> str:
    return provider.name or f"{gateway_name}-{spec.backend_slug}"


def _route_rule(logical: str, ref: ModelRef, backend_name: str) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "matches": [
            {
                "headers": [
                    {"type": "Exact", "name": "x-ai-eg-model", "value": logical},
                ],
            },
        ],
        "backendRefs": [{"name": backend_name}],
    }
    if ref.override:
        rule["backendRefs"][0]["modelNameOverride"] = ref.override
    elif ref.model != logical:
        # When the logical name differs from the provider's model name,
        # rewrite to the provider's name before sending upstream.
        rule["backendRefs"][0]["modelNameOverride"] = ref.model
    return rule


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


def _hostname(base_url: str, *, default: str) -> str:
    parsed = urlparse(base_url)
    return parsed.hostname or default
