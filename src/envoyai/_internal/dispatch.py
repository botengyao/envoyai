"""Dispatch ``Gateway.complete`` / ``acomplete`` through the local gateway.

Intentionally tiny. The gateway — running as an Envoy subprocess next to
this Python process — does routing, provider translation, retries,
fallbacks, and cost attribution. Python just speaks OpenAI-format HTTP to
the local port. That's the whole thing.

The real upstream API key never touches the Python client. The client
sends the literal string ``"unused"`` as its ``Authorization`` placeholder
so the OpenAI SDK doesn't complain; the gateway ignores it and injects the
real credential from its server-side auth policy before calling upstream.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence, Union

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion

    from envoyai._internal.runtime import LocalRun
    from envoyai.gateway import ChatMessage


__all__ = ["dispatch_sync", "dispatch_async"]


#: Placeholder key sent to the local gateway. Upstream auth is injected
#: server-side; the gateway ignores this value.
GATEWAY_PLACEHOLDER_KEY = "unused"


def dispatch_sync(
    run: "LocalRun",
    model: str,
    messages: Union[str, Sequence["ChatMessage"]],
    *,
    temperature: float | None,
    max_tokens: int | None,
    tools: Sequence[Mapping[str, Any]] | None,
    tool_choice: Union[str, Mapping[str, Any], None],
) -> "ChatCompletion":
    """Send one chat completion synchronously to the local gateway."""
    import openai

    client = openai.OpenAI(api_key=GATEWAY_PLACEHOLDER_KEY, base_url=run.base_url)
    return client.chat.completions.create(
        model=model,
        messages=_normalize_messages(messages),
        **_optional_kwargs(temperature, max_tokens, tools, tool_choice),
    )


async def dispatch_async(
    run: "LocalRun",
    model: str,
    messages: Union[str, Sequence["ChatMessage"]],
    *,
    temperature: float | None,
    max_tokens: int | None,
    tools: Sequence[Mapping[str, Any]] | None,
    tool_choice: Union[str, Mapping[str, Any], None],
) -> "ChatCompletion":
    """Send one chat completion asynchronously to the local gateway."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=GATEWAY_PLACEHOLDER_KEY, base_url=run.base_url
    )
    try:
        return await client.chat.completions.create(
            model=model,
            messages=_normalize_messages(messages),
            **_optional_kwargs(temperature, max_tokens, tools, tool_choice),
        )
    finally:
        await client.close()


def _normalize_messages(
    messages: Union[str, Sequence["ChatMessage"]],
) -> list[Mapping[str, Any]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return list(messages)


def _optional_kwargs(
    temperature: float | None,
    max_tokens: int | None,
    tools: Sequence[Mapping[str, Any]] | None,
    tool_choice: Union[str, Mapping[str, Any], None],
) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if temperature is not None:
        kw["temperature"] = temperature
    if max_tokens is not None:
        kw["max_tokens"] = max_tokens
    if tools is not None:
        kw["tools"] = list(tools)
    if tool_choice is not None:
        kw["tool_choice"] = tool_choice
    return kw
