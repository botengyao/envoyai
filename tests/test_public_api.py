"""Smoke tests for the public API surface. No network, no rendering — just
verify the types compose the way the README claims they do."""
from __future__ import annotations

import pytest

import envoyai as ea


def test_public_exports_present() -> None:
    # Top-level builder
    assert hasattr(ea, "Gateway")
    # All eight provider classes
    for name in (
        "OpenAI", "AzureOpenAI",
        "Bedrock", "AWSAnthropic",
        "Anthropic",
        "Cohere",
        "GCPVertex", "GCPAnthropic",
    ):
        assert hasattr(ea, name), name
    # Policy
    assert hasattr(ea, "RetryPolicy")
    assert hasattr(ea, "Budget")
    assert hasattr(ea, "Timeouts")
    # Auth helpers
    assert hasattr(ea, "env") and hasattr(ea, "secret") and hasattr(ea, "header")
    for ns in ("aws", "azure", "gcp"):
        assert hasattr(ea, ns), ns
    # Errors namespace
    assert hasattr(ea, "errors")


def test_builder_compiles_readme_example() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))
    bedrock = ea.Bedrock(region="us-east-1", credentials=ea.aws.irsa())

    gw = ea.Gateway("team-a")
    gw.model("chat").route(
        primary=openai("gpt-4o"),
        fallbacks=[bedrock("anthropic.claude-sonnet-4-20250514-v1:0")],
        retry=ea.RetryPolicy.rate_limit_tolerant(),
        timeout="60s",
    )
    # Validator should pass now that a primary is set.
    gw._validate()


def test_missing_primary_raises_config_error() -> None:
    gw = ea.Gateway("team-a")
    gw.model("chat")  # no .route() call
    with pytest.raises(ea.errors.ConfigError, match="primary"):
        gw._validate()


def test_retry_presets_return_retry_policy() -> None:
    for preset in (
        ea.RetryPolicy.rate_limit_tolerant(),
        ea.RetryPolicy.fail_fast(),
        ea.RetryPolicy.none(),
    ):
        assert isinstance(preset, ea.RetryPolicy)


def test_provider_is_callable_returning_model_ref() -> None:
    openai = ea.OpenAI(api_key=ea.env("OPENAI_KEY"))
    ref = openai("gpt-4o-mini", override="gpt-4o-mini-2024-07-18", weight=2)
    assert ref.model == "gpt-4o-mini"
    assert ref.override == "gpt-4o-mini-2024-07-18"
    assert ref.weight == 2
    assert ref.provider is openai


def test_privacy_defaults_are_safe() -> None:
    """Default Privacy redacts auth and does not log prompt/response bodies."""
    p = ea.Privacy()
    assert p.redact_auth is True
    assert p.log_prompts is False
    assert p.log_responses is False


def test_gateway_privacy_can_be_overridden() -> None:
    gw = ea.Gateway()
    openai = ea.OpenAI(api_key=ea.env("K"))
    gw.model("m").route(primary=openai("gpt-4o"))

    gw.privacy(ea.Privacy(redact_auth=True, log_prompts=True, log_responses=False))
    assert gw._privacy.log_prompts is True
    assert gw._privacy.log_responses is False


def test_alias_requires_existing_target() -> None:
    gw = ea.Gateway("t")
    openai = ea.OpenAI(api_key=ea.env("K"))
    gw.model("real").route(primary=openai("gpt-4o"))
    gw.alias("vendor-name", target="real")
    with pytest.raises(ea.errors.ModelNotFound):
        gw.alias("broken", target="does-not-exist")
