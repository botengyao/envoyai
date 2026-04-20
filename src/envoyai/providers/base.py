"""Provider base class and ModelRef.

A :class:`Provider` carries the identity and auth for an LLM service
(OpenAI, Bedrock, Azure, etc.). It is reusable — calling the instance with a
model name returns a :class:`ModelRef` usable in route configuration.
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class Provider(BaseModel):
    """Base class for every provider (OpenAI, Bedrock, Azure, Anthropic, …)."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str | None = None
    """Optional identifier for this provider instance. Auto-generated when unset."""

    _schema: ClassVar[str]

    def __call__(self, model: str, *, override: str | None = None, weight: int = 1) -> ModelRef:
        """Bind this provider to a specific model.

        Args:
            model: Provider model identifier, e.g. ``"gpt-4o-mini"`` or
                ``"anthropic.claude-sonnet-4-20250514-v1:0"``.
            override: Send a different model name to the provider than the
                client requested.
            weight: Share of traffic within a load-split routing tier.
        """
        return ModelRef(provider=self, model=model, override=override, weight=weight)


class ModelRef(BaseModel):
    """A provider bound to a specific model; used in route configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Provider
    model: str
    override: str | None = None
    weight: int = 1
