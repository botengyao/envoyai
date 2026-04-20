"""OpenAI and Azure OpenAI providers."""
from __future__ import annotations

from typing import ClassVar

from envoyai.auth import APIKeyRef
from envoyai.auth.azure import AzureCredential
from envoyai.providers.base import Provider


class OpenAI(Provider):
    """OpenAI hosted API (api.openai.com by default).

    Example::

        openai = envoyai.OpenAI(api_key=envoyai.env("OPENAI_KEY"))
        gw.model("chat").route(primary=openai("gpt-4o-mini"))

    Set ``base_url`` to target an OpenAI-compatible endpoint (vLLM, Ollama,
    self-hosted Together, etc.).
    """

    api_key: APIKeyRef
    base_url: str = "https://api.openai.com"

    _schema: ClassVar[str] = "OpenAI"


class AzureOpenAI(Provider):
    """Azure OpenAI Service.

    Example::

        azure = envoyai.AzureOpenAI(
            resource="myresource",
            api_version="2025-01-01-preview",
            credentials=envoyai.azure.api_key(envoyai.env("AZURE_KEY")),
        )
        gw.model("chat").route(primary=azure("gpt-4", override="my-deployment"))
    """

    resource: str
    """The Azure resource name; becomes ``{resource}.openai.azure.com``."""

    api_version: str = "2024-10-21"
    credentials: AzureCredential

    _schema: ClassVar[str] = "AzureOpenAI"
