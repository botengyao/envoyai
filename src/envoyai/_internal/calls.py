"""Module-level ``envoyai.complete()`` / ``envoyai.acomplete()`` bodies.

Thin forwarders that auto-register the model on the singleton Gateway and
dispatch to :meth:`envoyai.Gateway.complete` / :meth:`envoyai.Gateway.acomplete`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence, Union

from envoyai.gateway import ChatMessage

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion


def complete(
    model: str,
    messages: Union[str, Sequence[ChatMessage]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    tools: Sequence[Mapping[str, Any]] | None = None,
    tool_choice: Union[str, Mapping[str, Any], None] = None,
    provider_options: Mapping[str, Any] | None = None,
    timeout: str | None = None,
) -> "ChatCompletion":
    """Module-level shortcut: auto-register ``model`` and call the gateway.

    Uses a process-wide singleton :class:`envoyai.Gateway`, created lazily
    on first call and torn down at process exit. Bare model names are
    resolved through the catalog (``gpt-*`` → OpenAI, ``claude-*`` →
    Anthropic, ``anthropic.*`` / ``amazon.*`` / ``meta.*`` → Bedrock,
    ``command-*`` / ``rerank-*`` → Cohere). The explicit form
    ``"provider/model"`` (e.g. ``"openai/gpt-4o"``) is always accepted.

    Azure OpenAI and GCP Vertex AI cannot be auto-configured — build an
    explicit :class:`envoyai.Gateway` for those.

    Sync/async contract: this function is sync; see :func:`acomplete` for
    the async version. The return type never depends on kwargs.
    """
    from envoyai._internal.singleton import get_or_create, resolve_and_register

    logical = resolve_and_register(model)
    gw = get_or_create()
    return gw.complete(
        logical,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        tool_choice=tool_choice,
        provider_options=provider_options,
        timeout=timeout,
    )


async def acomplete(
    model: str,
    messages: Union[str, Sequence[ChatMessage]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    tools: Sequence[Mapping[str, Any]] | None = None,
    tool_choice: Union[str, Mapping[str, Any], None] = None,
    provider_options: Mapping[str, Any] | None = None,
    timeout: str | None = None,
) -> "ChatCompletion":
    """Async counterpart to :func:`complete`. Must be awaited."""
    from envoyai._internal.singleton import get_or_create, resolve_and_register

    logical = resolve_and_register(model)
    gw = get_or_create()
    return await gw.acomplete(
        logical,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        tool_choice=tool_choice,
        provider_options=provider_options,
        timeout=timeout,
    )
