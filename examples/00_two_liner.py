"""00 — Two-line quickstart.

The shortest possible use of envoyai. ``envoyai.complete()`` uses a
process-wide singleton Gateway under the hood, created lazily on first
call and torn down at process exit.

Bare model names auto-register through a small catalog:

    gpt-* / o1-* / o3-* / text-embedding-*   → OpenAI    ($OPENAI_API_KEY)
    claude-*                                  → Anthropic ($ANTHROPIC_API_KEY)
    anthropic.* / amazon.* / meta.* / ai21.* → Bedrock   (AWS default chain + $AWS_REGION)
    cohere.* / mistral.*                     → Bedrock
    command-* / rerank-*                     → Cohere    ($COHERE_API_KEY)

Explicit form ``"provider/model"`` always works:

    envoyai.complete(model="openai/gpt-5", messages=[...])

For Azure OpenAI, Vertex AI, custom base URLs, fallbacks, or anything
beyond the happy path, build an explicit :class:`envoyai.Gateway`.
"""
from __future__ import annotations

import envoyai as ea

resp = ea.complete(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hi in one word."}],
)
print(resp.choices[0].message.content)
