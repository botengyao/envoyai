"""Model-name prefix catalog for the ``envoyai.complete()`` shortcut.

Small, opinionated, and deliberately limited. Maps a bare model name like
``"gpt-4o"`` to the obvious provider family, and parses the explicit
``"provider/model"`` form.

If the catalog doesn't recognize a name, callers fall back to
:class:`envoyai.errors.ModelNotFound` and the user is directed to the
explicit form or an :class:`envoyai.Gateway`.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CatalogEntry",
    "PROVIDER_KEYS",
    "KNOWN_PREFIXES",
    "lookup_prefix",
    "parse_explicit",
]


@dataclass(frozen=True)
class CatalogEntry:
    """The provider family a model name maps to."""

    provider_key: str


#: The set of provider keys understood by ``provider/model`` explicit form.
PROVIDER_KEYS: frozenset[str] = frozenset(
    {"openai", "anthropic", "bedrock", "vertex", "cohere", "azure"}
)


# Match order matters — most specific prefix first.
KNOWN_PREFIXES: list[tuple[str, CatalogEntry]] = [
    # OpenAI
    ("gpt-", CatalogEntry("openai")),
    ("o1-", CatalogEntry("openai")),
    ("o3-", CatalogEntry("openai")),
    ("text-embedding-", CatalogEntry("openai")),
    ("dall-e-", CatalogEntry("openai")),
    # Anthropic (native API)
    ("claude-", CatalogEntry("anthropic")),
    # AWS Bedrock (vendor.family namespace)
    ("anthropic.", CatalogEntry("bedrock")),
    ("amazon.", CatalogEntry("bedrock")),
    ("meta.", CatalogEntry("bedrock")),
    ("ai21.", CatalogEntry("bedrock")),
    ("cohere.", CatalogEntry("bedrock")),
    ("mistral.", CatalogEntry("bedrock")),
    # GCP Vertex (Gemini)
    ("gemini-", CatalogEntry("vertex")),
    # Cohere native
    ("command-", CatalogEntry("cohere")),
    ("rerank-", CatalogEntry("cohere")),
]


def lookup_prefix(model: str) -> CatalogEntry | None:
    """Return the catalog entry for a bare model name, or ``None`` if unknown."""
    for prefix, entry in KNOWN_PREFIXES:
        if model.startswith(prefix):
            return entry
    return None


def parse_explicit(model: str) -> tuple[str, str] | None:
    """Parse the ``"provider/model"`` form.

    Returns ``(provider_key, upstream_model)`` if the model string has a
    recognized provider prefix, otherwise ``None``.
    """
    if "/" not in model:
        return None
    provider, _, upstream = model.partition("/")
    if provider in PROVIDER_KEYS and upstream:
        return (provider, upstream)
    return None
