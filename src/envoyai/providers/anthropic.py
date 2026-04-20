"""Native Anthropic API provider."""
from __future__ import annotations

from typing import ClassVar

from envoyai.auth import APIKeyRef
from envoyai.providers.base import Provider


class Anthropic(Provider):
    """Anthropic hosted API (api.anthropic.com).

    Example::

        anthropic = envoyai.Anthropic(api_key=envoyai.env("ANTHROPIC_KEY"))
        gw.model("chat").route(primary=anthropic("claude-sonnet-4"))
    """

    api_key: APIKeyRef
    base_url: str = "https://api.anthropic.com"

    _schema: ClassVar[str] = "Anthropic"
