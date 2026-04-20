"""GCP Vertex AI providers — Gemini and Anthropic-on-Vertex."""
from __future__ import annotations

from typing import ClassVar

from envoyai.auth.gcp import GCPCredential
from envoyai.providers.base import Provider


class GCPVertex(Provider):
    """Gemini on GCP Vertex AI.

    Example::

        vertex = envoyai.GCPVertex(
            project_id="my-project",
            region="us-central1",
            credentials=envoyai.gcp.workload_identity(
                project_id="my-project",
                pool="my-pool",
                provider="my-provider",
            ),
        )
        gw.model("chat").route(primary=vertex("gemini-2.5-flash"))
    """

    project_id: str
    region: str
    credentials: GCPCredential

    _schema: ClassVar[str] = "GCPVertexAI"


class GCPAnthropic(Provider):
    """Anthropic Messages API served via GCP Vertex AI."""

    project_id: str
    region: str
    credentials: GCPCredential

    _schema: ClassVar[str] = "GCPAnthropic"
