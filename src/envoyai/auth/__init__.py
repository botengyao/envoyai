"""Authentication helpers.

Top-level helpers (common):
    envoyai.env("VAR")                     — reference an env var
    envoyai.secret("name", key="apiKey")   — reference a k8s Secret
    envoyai.header("x-team")               — reference a request header (for identity/cost)

Per-cloud namespaces:
    envoyai.aws.irsa()                     — default AWS credential chain
    envoyai.aws.credentials_file(...)
    envoyai.aws.oidc(...)
    envoyai.azure.api_key(...)
    envoyai.azure.service_principal(...)
    envoyai.azure.oidc(...)
    envoyai.gcp.service_account(...)
    envoyai.gcp.workload_identity(...)
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class APIKeyRef(BaseModel):
    """Base class for any API-key-like reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EnvVar(APIKeyRef):
    """Resolve the key from an environment variable at render/run time."""

    var: str


class SecretRef(APIKeyRef):
    """Reference an existing Kubernetes Secret."""

    name: str
    namespace: str | None = None
    key: str = "apiKey"


class InlineKey(APIKeyRef):
    """Inline literal key value. Written into a generated Secret at render time.

    Avoid committing code that uses this; prefer ``env(...)`` or ``secret(...)``.
    """

    value: str


class Header(BaseModel):
    """Reference a request header value — used for identity and cost labels."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str


def env(var: str) -> EnvVar:
    """Reference an env var. At ``local()`` time it's substituted by ``aigw``; at
    ``render_k8s()`` time it's read and written into a generated Secret."""
    return EnvVar(var=var)


def secret(name: str, *, namespace: str | None = None, key: str = "apiKey") -> SecretRef:
    """Reference an existing Kubernetes Secret."""
    return SecretRef(name=name, namespace=namespace, key=key)


def header(name: str) -> Header:
    """Reference a request header, e.g. for per-team cost attribution."""
    return Header(name=name)


# Submodules import from this module, so they must be loaded after the classes
# above are defined.
from envoyai.auth import aws, azure, gcp  # noqa: E402

__all__ = [
    "env",
    "secret",
    "header",
    "EnvVar",
    "SecretRef",
    "InlineKey",
    "Header",
    "APIKeyRef",
    "aws",
    "azure",
    "gcp",
]
