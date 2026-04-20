"""envoyai — Python SDK for Envoy AI Gateway.

Define, run, and deploy an LLM gateway from Python. The SDK hides Envoy and
Kubernetes details behind a small set of concepts: Gateway, provider, model,
route, fallback, retry, budget.

Quick start:

    import envoyai as ea

    gw = ea.Gateway.quickstart()   # uses $OPENAI_API_KEY
    gw.local()

See https://github.com/botengyao/envoyai for docs.
"""

from envoyai._version import __version__

from envoyai.gateway import Gateway

from envoyai.providers import (
    Anthropic,
    AWSAnthropic,
    AzureOpenAI,
    Bedrock,
    Cohere,
    GCPAnthropic,
    GCPVertex,
    OpenAI,
)

from envoyai.auth import env, header, secret
from envoyai.auth import aws, azure, gcp

from envoyai.policy import Budget, Privacy, RetryPolicy, Timeouts

from envoyai import errors

__all__ = [
    "__version__",
    # Top-level builder
    "Gateway",
    # Providers
    "OpenAI",
    "AzureOpenAI",
    "Bedrock",
    "AWSAnthropic",
    "Anthropic",
    "Cohere",
    "GCPVertex",
    "GCPAnthropic",
    # Auth helpers
    "env",
    "secret",
    "header",
    "aws",
    "azure",
    "gcp",
    # Policy
    "RetryPolicy",
    "Budget",
    "Timeouts",
    "Privacy",
    # Errors namespace
    "errors",
]
