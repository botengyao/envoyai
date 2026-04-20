"""Tests for the aigw-standalone YAML renderer.

Renderer is pure: ``Gateway`` → list of manifest dicts → multi-doc YAML.
These tests pin the resource shapes and the support envelope (what raises
``NotImplementedError`` today).
"""
from __future__ import annotations

import pytest
import yaml

import envoyai as ea
from envoyai._internal.render.aigw_standalone import render_resources, render_yaml


def _openai_gateway() -> ea.Gateway:
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    return gw


def test_renders_expected_kinds() -> None:
    resources = render_resources(_openai_gateway())
    kinds = [r["kind"] for r in resources]
    # AIGatewayRoute, then provider stack, then Gateway CR.
    assert kinds[0] == "AIGatewayRoute"
    assert kinds[-1] == "Gateway"
    assert {
        "AIServiceBackend",
        "BackendSecurityPolicy",
        "Backend",
        "BackendTLSPolicy",
        "Secret",
    }.issubset(set(kinds))


def test_secret_uses_env_placeholder() -> None:
    (secret,) = [r for r in render_resources(_openai_gateway()) if r["kind"] == "Secret"]
    assert secret["stringData"]["apiKey"] == "${OPENAI_API_KEY}"


def test_route_matches_x_ai_eg_model_header() -> None:
    (route,) = [
        r for r in render_resources(_openai_gateway()) if r["kind"] == "AIGatewayRoute"
    ]
    rule = route["spec"]["rules"][0]
    match = rule["matches"][0]["headers"][0]
    assert match == {"type": "Exact", "name": "x-ai-eg-model", "value": "chat"}


def test_route_overrides_model_when_logical_name_differs() -> None:
    gw = ea.Gateway("team-a")
    gw.model("fast").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    (route,) = [r for r in render_resources(gw) if r["kind"] == "AIGatewayRoute"]
    rule = route["spec"]["rules"][0]
    assert rule["backendRefs"][0]["modelNameOverride"] == "gpt-4o-mini"


def test_route_keeps_explicit_override_when_provided() -> None:
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))(
            "gpt-4o", override="gpt-4o-2024-08-06"
        )
    )
    (route,) = [r for r in render_resources(gw) if r["kind"] == "AIGatewayRoute"]
    assert (
        route["spec"]["rules"][0]["backendRefs"][0]["modelNameOverride"]
        == "gpt-4o-2024-08-06"
    )


def test_gateway_cr_listens_on_configured_port() -> None:
    gw = ea.Gateway("team-a", port=18000)
    gw.model("chat").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    (gateway_cr,) = [r for r in render_resources(gw) if r["kind"] == "Gateway"]
    assert gateway_cr["spec"]["listeners"][0]["port"] == 18000


def test_backend_hostname_from_provider_base_url() -> None:
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=ea.OpenAI(
            api_key=ea.env("OPENAI_API_KEY"),
            base_url="https://my-proxy.example.com",
        )("gpt-4o-mini")
    )
    (backend,) = [
        r
        for r in render_resources(gw)
        if r["kind"] == "Backend" and r["apiVersion"].startswith("gateway.envoyproxy.io")
    ]
    assert backend["spec"]["endpoints"][0]["fqdn"]["hostname"] == "my-proxy.example.com"


def test_render_yaml_returns_multi_doc_string() -> None:
    text = render_yaml(_openai_gateway())
    docs = list(yaml.safe_load_all(text))
    assert len(docs) >= 6
    assert all(isinstance(d, dict) for d in docs)


# ---------------------------------------------------------------------------
# Support envelope — what raises NotImplementedError today
# ---------------------------------------------------------------------------


def test_non_openai_provider_is_not_supported_yet() -> None:
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))("claude-sonnet-4")
    )
    with pytest.raises(NotImplementedError, match="Anthropic"):
        render_resources(gw)


def test_fallbacks_not_supported_yet() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[openai("gpt-4o-mini")],
    )
    with pytest.raises(NotImplementedError, match="fallbacks"):
        render_resources(gw)


def test_split_primary_not_supported_yet() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(primary={openai("gpt-4o"): 9, openai("gpt-4o-mini"): 1})
    with pytest.raises(NotImplementedError, match="Split"):
        render_resources(gw)


def test_retry_not_supported_yet() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(primary=openai("gpt-4o"), retry=ea.RetryPolicy.fail_fast())
    with pytest.raises(NotImplementedError, match="RetryPolicy"):
        render_resources(gw)
