"""Provider base class and ModelRef.

A :class:`Provider` carries the identity and auth for an upstream LLM service
(OpenAI, Bedrock, Azure, etc.). It is reusable — calling the instance with a
model name returns a :class:`ModelRef` usable in route configuration.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from envoyai.providers.base import ModelRef as _ModelRefAlias  # noqa: F401


class Provider(BaseModel):
    """Base class for every upstream provider.

    Subclasses declare ``_schema`` as a ClassVar (e.g. ``"OpenAI"``,
    ``"AWSBedrock"``). The schema is an internal detail — users never refer to
    it directly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str | None = None
    """Optional backend identifier. Auto-generated from the provider type if unset."""

    _schema: ClassVar[str]

    def __call__(self, model: str, *, override: str | None = None, weight: int = 1) -> ModelRef:
        """Bind this provider to a specific upstream model.

        Args:
            model: Upstream model identifier, e.g. ``"gpt-4o-mini"`` or
                ``"anthropic.claude-sonnet-4-20250514-v1:0"``.
            override: Send a different model name upstream than clients request.
            weight: Weight within a load-split routing tier.
        """
        return ModelRef(provider=self, model=model, override=override, weight=weight)


class ModelRef(BaseModel):
    """A provider bound to a specific upstream model; used in route configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Provider
    model: str
    override: str | None = None
    weight: int = 1
