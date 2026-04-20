"""Process-wide implicit :class:`envoyai.Gateway` backing ``ea.complete()``.

The singleton is created lazily on first call and torn down via ``atexit``.
Routes are auto-registered based on the model name either through the
catalog (for bare names like ``"gpt-4o"``) or the explicit
``"provider/model"`` form.

Users who want explicit lifetime, multiple gateways, non-default auth, or
anything more than the happy path should build :class:`envoyai.Gateway`
directly.
"""
from __future__ import annotations

import atexit
import os
import threading
from typing import Optional

from envoyai._internal.catalog import lookup_prefix, parse_explicit
from envoyai.auth import env as _env_ref
from envoyai.errors import ConfigError, ModelNotFound
from envoyai.gateway import Gateway
from envoyai.providers.base import ModelRef

__all__ = ["get_or_create", "reset", "resolve_and_register"]


_create_lock = threading.Lock()
_register_lock = threading.Lock()
_singleton: Optional[Gateway] = None
_atexit_registered = False


def get_or_create() -> Gateway:
    """Return the process-wide singleton Gateway, creating it on first call.

    The singleton is marked as running against ``127.0.0.1:1975`` so the
    module-level ``envoyai.complete()`` / ``acomplete()`` path can dispatch
    immediately. Users are responsible for ensuring an ``aigw`` process is
    actually listening there (either started externally, or — once the
    runtime wrapper lands — automatically via this singleton).
    """
    from envoyai._internal.runtime import LocalRun

    global _singleton, _atexit_registered
    with _create_lock:
        if _singleton is None:
            _singleton = Gateway(name="envoyai-default")
            _singleton._running = LocalRun(port=1975, admin_port=1064)
            if not _atexit_registered:
                atexit.register(reset)
                _atexit_registered = True
        return _singleton


def reset() -> None:
    """Drop the singleton. Called at process exit; also used in tests.

    Safe to call when no singleton exists. When the runtime lands, this will
    also stop the background gateway subprocess.
    """
    global _singleton
    with _create_lock:
        _singleton = None


def resolve_and_register(model: str) -> str:
    """Ensure the singleton Gateway has a logical route for ``model``.

    Returns the logical name the caller should pass to ``Gateway.complete``.
    The logical name is the user-supplied model string itself, so callers
    don't have to track a second identifier.

    Raises
    ------
    envoyai.errors.ModelNotFound
        ``model`` doesn't match any catalog entry and isn't in
        ``provider/model`` form.
    envoyai.errors.ConfigError
        The required auth environment variables for the resolved provider
        are not set.
    """
    gw = get_or_create()
    with _register_lock:
        if model in gw._routes:
            return model
        provider_key, upstream_model = _parse_or_lookup(model)
        ref = _build_model_ref(provider_key, upstream_model)
        gw.model(model).route(primary=ref)
    return model


def _parse_or_lookup(model: str) -> tuple[str, str]:
    explicit = parse_explicit(model)
    if explicit is not None:
        return explicit
    entry = lookup_prefix(model)
    if entry is None:
        raise ModelNotFound(model)
    return (entry.provider_key, model)


def _build_model_ref(provider_key: str, upstream_model: str) -> ModelRef:
    """Build a :class:`ModelRef` for a known provider, reading auth from env.

    Only the simple key-based providers (OpenAI, Anthropic, Cohere) and
    Bedrock (via the AWS default credential chain) are auto-configurable.
    Azure and Vertex require too many site-specific inputs (resource name,
    API version, workload-identity pool, project, …) to infer from the
    environment, so they point the user at an explicit Gateway.
    """
    from envoyai import providers as _p
    from envoyai.auth import aws as _aws

    if provider_key == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise ConfigError(
                "OPENAI_API_KEY environment variable is not set; required for "
                f"envoyai.complete(model={upstream_model!r})"
            )
        return _p.OpenAI(api_key=_env_ref("OPENAI_API_KEY"))(upstream_model)

    if provider_key == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ConfigError(
                "ANTHROPIC_API_KEY environment variable is not set; required "
                f"for envoyai.complete(model={upstream_model!r})"
            )
        return _p.Anthropic(api_key=_env_ref("ANTHROPIC_API_KEY"))(upstream_model)

    if provider_key == "cohere":
        if not os.environ.get("COHERE_API_KEY"):
            raise ConfigError(
                "COHERE_API_KEY environment variable is not set; required for "
                f"envoyai.complete(model={upstream_model!r})"
            )
        return _p.Cohere(api_key=_env_ref("COHERE_API_KEY"))(upstream_model)

    if provider_key == "bedrock":
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not region:
            raise ConfigError(
                "AWS_REGION (or AWS_DEFAULT_REGION) is not set; required for "
                f"envoyai.complete(model={upstream_model!r})"
            )
        return _p.Bedrock(region=region, credentials=_aws.irsa())(upstream_model)

    if provider_key == "azure":
        raise ConfigError(
            "Azure OpenAI cannot be auto-configured by envoyai.complete(): "
            "it needs a resource name and API version. Build an explicit "
            "Gateway:\n"
            "    gw = ea.Gateway()\n"
            "    azure = ea.AzureOpenAI(resource=..., api_version=..., "
            "credentials=ea.azure.api_key(ea.env('AZURE_OPENAI_API_KEY')))\n"
            f"    gw.model('chat').route(primary=azure({upstream_model!r}))\n"
            "    gw.local()"
        )

    if provider_key == "vertex":
        raise ConfigError(
            "GCP Vertex AI cannot be auto-configured by envoyai.complete(): "
            "it needs a project id, region, and workload-identity details. "
            "Build an explicit Gateway:\n"
            "    gw = ea.Gateway()\n"
            "    vertex = ea.GCPVertex(project_id=..., region=..., "
            "credentials=ea.gcp.workload_identity(...))\n"
            f"    gw.model('chat').route(primary=vertex({upstream_model!r}))\n"
            "    gw.local()"
        )

    raise ConfigError(  # pragma: no cover — caller already filtered provider_key
        f"internal error: unknown provider key {provider_key!r}"
    )
