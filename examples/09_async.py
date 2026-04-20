"""09 — Async completions.

Use ``openai.AsyncOpenAI`` for async calls. envoyai itself is synchronous at
the builder level; all async concerns live in the data-plane client.
"""
from __future__ import annotations

import asyncio

import envoyai as ea
from openai import AsyncOpenAI

gw = ea.Gateway()
gw.model("chat").route(primary=ea.OpenAI(api_key=ea.env("OPENAI_KEY"))("gpt-4o-mini"))
gw.local()


async def main() -> None:
    client = AsyncOpenAI(base_url="http://localhost:1975", api_key="unused")

    # Fan out three requests concurrently.
    prompts = ["haiku about coffee", "haiku about Python", "haiku about winter"]
    coros = [
        client.chat.completions.create(
            model="chat",
            messages=[{"role": "user", "content": p}],
        )
        for p in prompts
    ]
    for resp in await asyncio.gather(*coros):
        print(resp.choices[0].message.content, "\n---")


asyncio.run(main())
