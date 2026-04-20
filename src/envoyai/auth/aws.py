"""AWS credential helpers for Bedrock and related providers.

Three modes, matching the gateway's supported auth methods:

- :func:`irsa` — the AWS default credential chain (env vars, EKS Pod Identity,
  IRSA, EC2 instance roles). No Secret needed.
- :func:`credentials_file` — a Kubernetes Secret holding an INI credentials file.
- :func:`oidc` — OIDC federation: exchange an identity token for temporary AWS
  credentials via ``sts:AssumeRoleWithWebIdentity``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from envoyai.auth import SecretRef

__all__ = [
    "AWSCredential",
    "IRSA",
    "CredentialsFile",
    "OIDC",
    "irsa",
    "credentials_file",
    "oidc",
]


class AWSCredential(BaseModel):
    """Base for any AWS credential mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class IRSA(AWSCredential):
    """Rely on the AWS default credential chain."""


class CredentialsFile(AWSCredential):
    """Read ~/.aws/credentials-style INI from a Kubernetes Secret."""

    secret: SecretRef
    profile: str = "default"


class OIDC(AWSCredential):
    """Exchange an OIDC identity token for temporary AWS credentials."""

    role_arn: str
    oidc_issuer: str
    oidc_client_id: str
    audience: str | None = None


def irsa() -> IRSA:
    return IRSA()


def credentials_file(secret: SecretRef, *, profile: str = "default") -> CredentialsFile:
    return CredentialsFile(secret=secret, profile=profile)


def oidc(
    *,
    role_arn: str,
    oidc_issuer: str,
    oidc_client_id: str,
    audience: str | None = None,
) -> OIDC:
    return OIDC(
        role_arn=role_arn,
        oidc_issuer=oidc_issuer,
        oidc_client_id=oidc_client_id,
        audience=audience,
    )
