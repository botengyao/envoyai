"""05 — Streaming completions.

Once the gateway is up, streaming is the standard OpenAI SDK call — envoyai
passes the streaming response through from the provider unchanged.
"""
from __future__ import annotations

import envoyai as ea
from openai import OpenAI

gw = ea.Gateway()
gw.model("chat").route(primary=ea.OpenAI(api_key=ea.env("OPENAI_KEY"))("gpt-4o"))
gw.local()

client = OpenAI(base_url="http://localhost:1975", api_key="unused")

stream = client.chat.completions.create(
    model="chat",
    messages=[{"role": "user", "content": "List three colors, one per line."}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
