"""GCP credential helpers for Vertex AI and related providers."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from envoyai.auth import SecretRef

__all__ = [
    "GCPCredential",
    "ServiceAccount",
    "WorkloadIdentity",
    "service_account",
    "workload_identity",
]


class GCPCredential(BaseModel):
    """Base for GCP credential modes."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ServiceAccount(GCPCredential):
    """Service-account JSON key held in a Kubernetes Secret."""

    secret: SecretRef


class WorkloadIdentity(GCPCredential):
    """Workload Identity Federation: exchange an external identity for GCP creds."""

    project_id: str
    pool: str
    provider: str
    service_account_email: str | None = None
    audience: str | None = None


def service_account(secret: SecretRef) -> ServiceAccount:
    return ServiceAccount(secret=secret)


def workload_identity(
    *,
    project_id: str,
    pool: str,
    provider: str,
    service_account_email: str | None = None,
    audience: str | None = None,
) -> WorkloadIdentity:
    return WorkloadIdentity(
        project_id=project_id,
        pool=pool,
        provider=provider,
        service_account_email=service_account_email,
        audience=audience,
    )
