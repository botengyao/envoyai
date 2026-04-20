"""Cohere provider."""
from __future__ import annotations

from typing import ClassVar

from envoyai.auth import APIKeyRef
from envoyai.providers.base import Provider


class Cohere(Provider):
    """Cohere hosted API (api.cohere.com).

    Example::

        cohere = envoyai.Cohere(api_key=envoyai.env("COHERE_KEY"))
        gw.model("rerank").route(primary=cohere("command-r-plus"))
    """

    api_key: APIKeyRef
    base_url: str = "https://api.cohere.com"
    api_version: str | None = None

    _schema: ClassVar[str] = "Cohere"
