"""Azure credential helpers for Azure OpenAI and related providers."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from envoyai.auth import APIKeyRef, SecretRef

__all__ = [
    "AzureCredential",
    "APIKey",
    "ServicePrincipal",
    "OIDC",
    "api_key",
    "service_principal",
    "oidc",
]


class AzureCredential(BaseModel):
    """Base for Azure credential modes."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class APIKey(AzureCredential):
    """Azure-style API key (injected as the ``api-key`` header)."""

    key: APIKeyRef


class ServicePrincipal(AzureCredential):
    """Azure AD service principal (client_id + tenant_id + client_secret)."""

    client_id: str
    tenant_id: str
    client_secret: APIKeyRef


class OIDC(AzureCredential):
    """Azure OIDC federation (workload identity)."""

    client_id: str
    tenant_id: str
    oidc_issuer: str
    oidc_client_id: str
    audience: str | None = None


def api_key(key: APIKeyRef) -> APIKey:
    return APIKey(key=key)


def service_principal(
    *,
    client_id: str,
    tenant_id: str,
    client_secret: APIKeyRef | SecretRef,
) -> ServicePrincipal:
    return ServicePrincipal(
        client_id=client_id,
        tenant_id=tenant_id,
        client_secret=client_secret,
    )


def oidc(
    *,
    client_id: str,
    tenant_id: str,
    oidc_issuer: str,
    oidc_client_id: str,
    audience: str | None = None,
) -> OIDC:
    return OIDC(
        client_id=client_id,
        tenant_id=tenant_id,
        oidc_issuer=oidc_issuer,
        oidc_client_id=oidc_client_id,
        audience=audience,
    )
