"""Providers — backend identity + authentication.

Each provider instance is reusable across many logical models. Call the
instance to bind it to a specific upstream model name:

    openai = envoyai.OpenAI(api_key=envoyai.env("OPENAI_KEY"))
    gw.model("chat").route(primary=openai("gpt-4o-mini"))
    gw.model("fast").route(primary=openai("gpt-4o-mini", override="gpt-4o-mini-2024-07-18"))
"""
from __future__ import annotations

from envoyai.providers.anthropic import Anthropic
from envoyai.providers.base import ModelRef, Provider
from envoyai.providers.bedrock import AWSAnthropic, Bedrock
from envoyai.providers.cohere import Cohere
from envoyai.providers.gcp import GCPAnthropic, GCPVertex
from envoyai.providers.openai import AzureOpenAI, OpenAI

__all__ = [
    "Provider",
    "ModelRef",
    "OpenAI",
    "AzureOpenAI",
    "Bedrock",
    "AWSAnthropic",
    "Anthropic",
    "Cohere",
    "GCPVertex",
    "GCPAnthropic",
]
