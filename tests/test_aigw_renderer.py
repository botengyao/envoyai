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


def _anthropic_gateway() -> ea.Gateway:
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))("claude-sonnet-4")
    )
    return gw


def test_anthropic_renders_expected_kinds() -> None:
    resources = render_resources(_anthropic_gateway())
    kinds = [r["kind"] for r in resources]
    assert kinds[0] == "AIGatewayRoute"
    assert kinds[-1] == "Gateway"
    assert {
        "AIServiceBackend",
        "BackendSecurityPolicy",
        "Backend",
        "BackendTLSPolicy",
        "Secret",
    }.issubset(set(kinds))


def test_anthropic_aiservicebackend_uses_anthropic_schema() -> None:
    (svc,) = [
        r
        for r in render_resources(_anthropic_gateway())
        if r["kind"] == "AIServiceBackend"
    ]
    assert svc["spec"]["schema"]["name"] == "Anthropic"


def test_anthropic_security_policy_uses_native_auth_type() -> None:
    (sec,) = [
        r
        for r in render_resources(_anthropic_gateway())
        if r["kind"] == "BackendSecurityPolicy"
    ]
    # Anthropic requires a distinct type + field (the gateway injects the
    # x-api-key header rather than Authorization).
    assert sec["spec"]["type"] == "AnthropicAPIKey"
    assert "anthropicAPIKey" in sec["spec"]
    assert "apiKey" not in sec["spec"]


def test_anthropic_backend_hostname_defaults_to_api_anthropic_com() -> None:
    (backend,) = [
        r
        for r in render_resources(_anthropic_gateway())
        if r["kind"] == "Backend"
        and r["apiVersion"].startswith("gateway.envoyproxy.io")
    ]
    assert (
        backend["spec"]["endpoints"][0]["fqdn"]["hostname"]
        == "api.anthropic.com"
    )


def test_anthropic_backend_name_uses_anthropic_slug() -> None:
    (svc,) = [
        r
        for r in render_resources(_anthropic_gateway())
        if r["kind"] == "AIServiceBackend"
    ]
    assert svc["metadata"]["name"].endswith("-anthropic")


def test_anthropic_secret_uses_env_placeholder() -> None:
    (secret,) = [
        r for r in render_resources(_anthropic_gateway()) if r["kind"] == "Secret"
    ]
    assert secret["stringData"]["apiKey"] == "${ANTHROPIC_API_KEY}"


def test_openai_and_anthropic_coexist_in_one_gateway() -> None:
    gw = ea.Gateway("team-a")
    gw.model("fast").route(
        primary=ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))("gpt-4o-mini")
    )
    gw.model("smart").route(
        primary=ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))("claude-sonnet-4")
    )
    resources = render_resources(gw)
    schemas = {
        r["spec"]["schema"]["name"]
        for r in resources
        if r["kind"] == "AIServiceBackend"
    }
    assert schemas == {"OpenAI", "Anthropic"}

    backend_names = {r["metadata"]["name"] for r in resources if r["kind"] == "Backend"}
    assert any(n.endswith("-openai") for n in backend_names)
    assert any(n.endswith("-anthropic") for n in backend_names)


def test_unsupported_provider_lists_what_is_supported() -> None:
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=ea.Bedrock(region="us-east-1", credentials=ea.aws.irsa())(
            "anthropic.claude-sonnet-4-20250514-v1:0"
        )
    )
    with pytest.raises(NotImplementedError) as excinfo:
        render_resources(gw)
    msg = str(excinfo.value)
    assert "Bedrock" in msg
    # Error message names the providers that *are* supported.
    assert "OpenAI" in msg
    assert "Anthropic" in msg


def test_split_primary_not_supported_yet() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(primary={openai("gpt-4o"): 9, openai("gpt-4o-mini"): 1})
    with pytest.raises(NotImplementedError, match="Split"):
        render_resources(gw)


# ---------------------------------------------------------------------------
# Fallback + RetryPolicy rendering (BackendTrafficPolicy)
# ---------------------------------------------------------------------------


def test_single_primary_omits_priority_and_btp() -> None:
    """Backwards-compatible: no fallbacks and no retry → no BTP doc, no
    ``priority`` field on the single backendRef."""
    resources = render_resources(_openai_gateway())
    kinds = [r["kind"] for r in resources]
    assert "BackendTrafficPolicy" not in kinds
    (route,) = [r for r in resources if r["kind"] == "AIGatewayRoute"]
    (ref,) = route["spec"]["rules"][0]["backendRefs"]
    assert "priority" not in ref


def test_fallback_emits_prioritized_backend_refs() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    anthropic = ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[anthropic("claude-sonnet-4")],
    )
    (route,) = [r for r in render_resources(gw) if r["kind"] == "AIGatewayRoute"]
    refs = route["spec"]["rules"][0]["backendRefs"]
    assert len(refs) == 2
    assert refs[0]["priority"] == 0
    assert refs[1]["priority"] == 1
    assert refs[0]["name"].endswith("-openai")
    assert refs[1]["name"].endswith("-anthropic")
    # modelNameOverride preserved per-slot.
    assert refs[0]["modelNameOverride"] == "gpt-4o"
    assert refs[1]["modelNameOverride"] == "claude-sonnet-4"


def test_fallback_without_explicit_retry_still_emits_btp() -> None:
    """If you declare fallbacks, failover has to actually fire — which
    means a BTP with retry triggers. Injecting a sane default is better
    than silently producing a chain that never walks."""
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    anthropic = ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[anthropic("claude-sonnet-4")],
    )
    (btp,) = [r for r in render_resources(gw) if r["kind"] == "BackendTrafficPolicy"]
    retry = btp["spec"]["retry"]
    assert retry["numAttemptsPerPriority"] == 1
    # Default failover retries on connection errors and 5xx.
    triggers = set(retry["retryOn"]["triggers"])
    assert {"connect-failure", "retriable-status-codes"}.issubset(triggers)
    assert 500 in retry["retryOn"]["httpStatusCodes"]


def test_btp_targets_httproute_with_gateway_name() -> None:
    """aigw generates an HTTPRoute whose name matches the AIGatewayRoute;
    the BTP must target that name, not the Gateway CR."""
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"), retry=ea.RetryPolicy.fail_fast()
    )
    (btp,) = [r for r in render_resources(gw) if r["kind"] == "BackendTrafficPolicy"]
    (target,) = btp["spec"]["targetRefs"]
    assert target == {
        "group": "gateway.networking.k8s.io",
        "kind": "HTTPRoute",
        "name": "team-a",
    }


def test_retry_policy_translates_attempts() -> None:
    """``attempts`` is total attempts across the chain; aigw's numRetries
    is the retry count, i.e. attempts - 1."""
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        retry=ea.RetryPolicy(attempts=5, attempts_per_step=2, on=["rate_limit"]),
    )
    (btp,) = [r for r in render_resources(gw) if r["kind"] == "BackendTrafficPolicy"]
    retry = btp["spec"]["retry"]
    assert retry["numRetries"] == 4
    assert retry["numAttemptsPerPriority"] == 2


def test_retry_policy_rate_limit_reasons_map_to_429() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        retry=ea.RetryPolicy(on=["rate_limit"]),
    )
    (btp,) = [r for r in render_resources(gw) if r["kind"] == "BackendTrafficPolicy"]
    retry_on = btp["spec"]["retry"]["retryOn"]
    assert retry_on["httpStatusCodes"] == [429]
    assert "envoy-ratelimited" in retry_on["triggers"]
    # retriable-status-codes is required for any listed httpStatusCodes to
    # actually trigger a retry.
    assert "retriable-status-codes" in retry_on["triggers"]


def test_retry_policy_fail_fast_emits_inert_btp() -> None:
    """fail_fast = 1 attempt, no retry reasons. aigw still needs retryOn
    populated, but with numRetries=0 nothing fires."""
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"), retry=ea.RetryPolicy.fail_fast()
    )
    (btp,) = [r for r in render_resources(gw) if r["kind"] == "BackendTrafficPolicy"]
    retry = btp["spec"]["retry"]
    assert retry["numRetries"] == 0
    assert retry["retryOn"]  # non-empty (aigw requires it)


def test_retry_policy_backoff_and_timeout_forwarded() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        retry=ea.RetryPolicy(
            attempts=2,
            per_retry_timeout="45s",
            backoff_base="250ms",
            backoff_max="20s",
            on=["server_error"],
        ),
    )
    (btp,) = [r for r in render_resources(gw) if r["kind"] == "BackendTrafficPolicy"]
    per = btp["spec"]["retry"]["perRetry"]
    assert per["timeout"] == "45s"
    assert per["backOff"]["baseInterval"] == "250ms"
    assert per["backOff"]["maxInterval"] == "20s"


def test_fallback_plus_retry_policy_combines() -> None:
    """Fallback chain + explicit RetryPolicy: the BTP carries the user's
    policy (not the injected failover default), and backendRefs stay
    prioritized."""
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    anthropic = ea.Anthropic(api_key=ea.env("ANTHROPIC_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[anthropic("claude-sonnet-4")],
        retry=ea.RetryPolicy.rate_limit_tolerant(),
    )
    resources = render_resources(gw)
    (route,) = [r for r in resources if r["kind"] == "AIGatewayRoute"]
    assert [ref["priority"] for ref in route["spec"]["rules"][0]["backendRefs"]] == [0, 1]
    (btp,) = [r for r in resources if r["kind"] == "BackendTrafficPolicy"]
    retry = btp["spec"]["retry"]
    # rate_limit_tolerant preset: attempts=5, on=[rate_limit, server_error]
    assert retry["numRetries"] == 4
    status = set(retry["retryOn"]["httpStatusCodes"])
    assert 429 in status
    assert 500 in status


def test_fallback_same_provider_deduplicates_backend() -> None:
    """Primary and fallback on the same provider instance emit one
    AIServiceBackend — fallback pricing and auth don't change by
    referencing the same upstream twice."""
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[openai("gpt-4o-mini")],
    )
    resources = render_resources(gw)
    backends = [r for r in resources if r["kind"] == "AIServiceBackend"]
    assert len(backends) == 1
    (route,) = [r for r in resources if r["kind"] == "AIGatewayRoute"]
    refs = route["spec"]["rules"][0]["backendRefs"]
    # Both refs share the backend; the modelNameOverride disambiguates.
    assert refs[0]["name"] == refs[1]["name"]
    assert refs[0]["modelNameOverride"] == "gpt-4o"
    assert refs[1]["modelNameOverride"] == "gpt-4o-mini"


def test_differing_retry_policies_across_routes_rejected() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"), retry=ea.RetryPolicy.fail_fast()
    )
    gw.model("other").route(
        primary=openai("gpt-4o-mini"), retry=ea.RetryPolicy.rate_limit_tolerant()
    )
    with pytest.raises(NotImplementedError, match="RetryPolicy"):
        render_resources(gw)


def test_same_retry_policy_across_routes_is_fine() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    policy = ea.RetryPolicy.fail_fast()
    gw = ea.Gateway("team-a")
    gw.model("chat").route(primary=openai("gpt-4o"), retry=policy)
    gw.model("other").route(primary=openai("gpt-4o-mini"), retry=policy)
    resources = render_resources(gw)
    btps = [r for r in resources if r["kind"] == "BackendTrafficPolicy"]
    assert len(btps) == 1


def test_fallback_split_not_supported_yet() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_API_KEY"))
    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[{openai("gpt-4o-mini"): 9, openai("gpt-3.5-turbo"): 1}],
    )
    with pytest.raises(NotImplementedError, match="Split"):
        render_resources(gw)
